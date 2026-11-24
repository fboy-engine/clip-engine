"""A3b: stable-overlay cam detector, the fallback when the border-line detector (camfind) is not confident.

Many streams draw no border around the cam: the cam is only a rectangle where the picture changes hard. The game behind it
changes from frame to frame, the rectangle's edge does not move. So: sample many frames, measure the hard picture-change edges
(a step in brightness over a few pixels) in every frame, and keep only the edges that sit at the SAME place in most frames
(`persistent_steps`). The cam is the rectangle those edges (or the frame edge) enclose around a STABLE FACE, and the game outside
the rectangle must really be changing, else a static scene would pass as an overlay. Evidence only: it raises
camfind.CamNotConfident (every reason in the message) instead of guessing, and it never takes a position or size as a hint.
Plain gray uint8 stacks in, so it is testable without ffmpeg or a video file.
"""
import numpy as np

from . import camfind as cf

STEP_K = 6              # px averaged on each side of a candidate edge
STEP_THR = 9.0          # gray levels of picture change that make an edge "hard"
PERSIST = 0.5           # share of the sampled frames an edge must be present in at the same place
MIN_OUTSIDE_MOTION = 2.0    # mean gray change between sampled frames outside the cam: below it the game is not changing
OUTER_PREFERENCE = 0.8  # an outer line scoring >= 80% of the best one wins (a wall edge inside the cam must not shrink the cam)


def persistent_steps(gray_frames, k=STEP_K, thr=STEP_THR, frac=PERSIST, minlen_frac=0.08):
    """Hard picture-change edges at the same place in >= `frac` of the frames.
    -> (vlines [{x, y0, y1, persist, strength}], hlines [{y, x0, x1, persist, strength}]); same keys as camfind.persistent_lines
    plus persist (mean share of frames) and strength (mean gray step)."""
    cv2 = cf._cv2()
    n, (h, w) = len(gray_frames), gray_frames[0].shape
    hk = k // 2
    cnt = [np.zeros((h, w), np.uint8), np.zeros((h, w), np.uint8)]
    acc = [np.zeros((h, w), np.float32), np.zeros((h, w), np.float32)]
    for g in gray_frames:
        f = g.astype(np.float32)
        for a, (kern, ax) in enumerate((((k, 1), 1), ((1, k), 0))):
            m = cv2.blur(f, kern)
            s = np.zeros((h, w), np.float32)
            if ax == 1:
                s[:, hk:w - hk] = np.abs(m[:, 2 * hk:] - m[:, :w - 2 * hk])
            else:
                s[hk:h - hk, :] = np.abs(m[2 * hk:, :] - m[:h - 2 * hk, :])
            cnt[a] += (s > thr).astype(np.uint8)
            acc[a] += s
    need = int(np.ceil(frac * n))
    res = []
    for a, vertical in enumerate((True, False)):
        m = (cnt[a] >= need).astype(np.uint8)
        ml, gap = int(minlen_frac * (h if vertical else w)), max(3, int(0.015 * (h if vertical else w)))
        shape = (1, gap) if vertical else (gap, 1)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, shape))
        ke = cv2.getStructuringElement(cv2.MORPH_RECT, (1, ml) if vertical else (ml, 1))
        nl, lab, st, _ = cv2.connectedComponentsWithStats(cv2.morphologyEx(m, cv2.MORPH_OPEN, ke), connectivity=8)
        segs = []
        for i in range(1, nl):
            x, y, cw, ch, _a = [int(v) for v in st[i]]
            if (cw if vertical else ch) > 3 * k:            # a blob, not a line
                continue
            sel = lab[y:y + ch, x:x + cw] == i
            segs.append({("x" if vertical else "y"): float(x + cw / 2 if vertical else y + ch / 2),
                         ("y0" if vertical else "x0"): float(y if vertical else x),
                         ("y1" if vertical else "x1"): float(y + ch if vertical else x + cw),
                         "persist": round(float(cnt[a][y:y + ch, x:x + cw][sel].mean()) / n, 3),
                         "strength": round(float(acc[a][y:y + ch, x:x + cw][sel].mean()) / n, 2)})
        key, lo, hi = ("x", "y0", "y1") if vertical else ("y", "x0", "x1")
        merged = cf._merge(segs, key, lo, hi, 0.1 * (h if vertical else w))
        res.append(merged)
    return res[0], res[1]


def _score(line, lo, hi, a, b):
    """Length of `line` inside [a, b] along its axis x persistence x contrast (capped at twice the hard-edge threshold)."""
    length = max(0.0, min(line[hi], b) - max(line[lo], a))
    return length * line["persist"] * min(1.0, line["strength"] / (2 * STEP_THR))


def _pick(lines, lo, hi, a, b):
    """Best line by score; an outer line scoring >= OUTER_PREFERENCE of it wins. `lines` are ordered inner -> outer."""
    scored = [(_score(l, lo, hi, a, b), l) for l in lines]
    scored = [s for s in scored if s[0] > 0]
    if not scored:
        return None
    top = max(s[0] for s in scored)
    ok = [l for s, l in scored if s >= OUTER_PREFERENCE * top]
    return ok[-1]


def _grow(f, vl, hl, w_src, h_src):
    """Rectangle around face track `f` from the persistent hard edges, or the frame edge. -> (rect, sides, lines)."""
    cx, cy, fw = f["cx"], f["cy"], f["w"]
    tol = 0.03 * h_src
    left = sorted([l for l in vl if l["x"] < cx - 0.6 * fw and l["y0"] - tol <= cy <= l["y1"] + tol and cx - l["x"] <= 0.5 * w_src],
                  key=lambda l: -l["x"])
    right = sorted([l for l in vl if l["x"] > cx + 0.6 * fw and l["y0"] - tol <= cy <= l["y1"] + tol and l["x"] - cx <= 0.5 * w_src],
                   key=lambda l: l["x"])
    L, R = _pick(left, "y0", "y1", cy - 2.5 * fw, cy + 2.5 * fw), _pick(right, "y0", "y1", cy - 2.5 * fw, cy + 2.5 * fw)
    x0, x1 = (L["x"] if L else 0.0), (R["x"] if R else float(w_src))
    up = sorted([l for l in hl if l["y"] < cy - 0.6 * fw and cy - l["y"] <= 0.6 * h_src and
                 min(l["x1"], x1) - max(l["x0"], x0) >= 0.45 * (x1 - x0)], key=lambda l: -l["y"])
    dn = sorted([l for l in hl if l["y"] > cy + 0.6 * fw and l["y"] - cy <= 0.6 * h_src and
                 min(l["x1"], x1) - max(l["x0"], x0) >= 0.45 * (x1 - x0)], key=lambda l: l["y"])
    T, B = _pick(up, "x0", "x1", x0, x1), _pick(dn, "x0", "x1", x0, x1)
    y0, y1 = (T["y"] if T else 0.0), (B["y"] if B else float(h_src))
    sides = {"left": "line" if L else "edge", "right": "line" if R else "edge",
             "top": "line" if T else "edge", "bottom": "line" if B else "edge"}
    return (x0, y0, x1, y1), sides, [l for l in (L, R, T, B) if l]


def outside_motion(gray_frames, rect, f=4):
    """Mean gray change between consecutive sampled frames OUTSIDE the cam rectangle (1/f resolution): is the game behind it changing?"""
    cv2 = cf._cv2()
    h, w = gray_frames[0].shape
    small = [cv2.resize(g, (w // f, h // f), interpolation=cv2.INTER_AREA).astype(np.float32) for g in gray_frames]
    mask = np.ones(small[0].shape, bool)
    mask[int(rect[1] // f):int(rect[3] // f) + 1, int(rect[0] // f):int(rect[2] // f) + 1] = False
    if not mask.any():
        return 0.0
    d = [np.abs(a - b)[mask].mean() for a, b in zip(small[1:], small[:-1])]
    return float(np.mean(d)) if d else 0.0


def candidates(faces, vl, hl, w_src, h_src, motion_fn, min_hit=0.35):
    """Scored cam rectangles, best first (same fields as camfind.cam_candidates, plus edge persistence and game motion)."""
    out = []
    for f in faces:
        if f["hit"] < min_hit:
            continue
        rect, sides, lines = _grow(f, vl, hl, w_src, h_src)
        rw, rh = rect[2] - rect[0], rect[3] - rect[1]
        real = len(lines)
        edge = 4 - real
        area = rw * rh / float(w_src * h_src)
        motion = motion_fn(rect)
        reasons = []
        if real < 1:
            reasons.append("no hard picture-change edge that stays in place around the face")
        if edge > 2:
            reasons.append(f"{edge} of 4 sides have no stable edge and no frame edge to stop at")
        if not 0.012 <= area <= 0.42:
            reasons.append(f"rectangle covers {100 * area:.1f}% of the frame (a cam is 1.2-42%)")
        if f["w"] / max(rw, 1.0) > 0.6 or f["w"] / max(rw, 1.0) < 0.04:
            reasons.append(f"face is {100 * f['w'] / max(rw, 1.0):.0f}% of the rectangle width")
        if not 0.5 <= rw / max(rh, 1.0) <= 3.6:
            reasons.append(f"rectangle aspect {rw / max(rh, 1.0):.2f} is not a cam shape")
        if motion < MIN_OUTSIDE_MOTION:
            reasons.append(f"the picture outside the rectangle barely changes (mean {motion:.1f} gray levels): "
                           "cannot tell an overlay from a still scene")
        hit, stab = min(1.0, f["hit"] / 0.8), max(0.0, 1.0 - f["spread"] / 0.5)
        q = [min(1.0, l["persist"]) * min(1.0, l["strength"] / (2 * STEP_THR)) for l in lines]
        sup = min(1.0, (sum(q) + 0.5 * edge) / 3.0) if real else 0.0
        size = 1.0 if 0.05 <= f["w"] / max(rw, 1.0) <= 0.5 else 0.5
        conf = 0.35 * hit + 0.15 * stab + 0.35 * sup + 0.15 * size
        if reasons:
            conf = min(conf, cf.MIN_CONFIDENCE - 0.01)
        out.append({"rect": tuple(round(float(v), 1) for v in rect), "sides": sides, "confidence": round(conf, 3), "reasons": reasons,
                    "face": f, "area_pct": round(100 * area, 2), "edge_persist": round(float(np.mean([l["persist"] for l in lines])), 3)
                    if lines else 0.0, "outside_motion": round(motion, 2)})
    out.sort(key=lambda c: -c["confidence"])
    dedup = []
    for c in out:
        if all(cf._iou(c["rect"], d["rect"]) < 0.5 for d in dedup):
            dedup.append(c)
    return dedup


def detect(gray_frames, face_fn=None, min_conf=cf.MIN_CONFIDENCE):
    """Cam rectangle + overlay zones from sampled gray frames by the stable-overlay rule. Same result shape as camfind.detect, plus
    method, face_hit_pct and pinned {edge_persist, outside_motion}. Raises CamNotConfident (with every reason) instead of guessing."""
    n = len(gray_frames)
    if n < 6:
        raise cf.CamNotConfident(f"only {n} frames sampled: need at least 6 to tell an overlay from game content")
    h_src, w_src = gray_frames[0].shape
    faces = cf.cluster_faces((face_fn or cf.haar_faces)(gray_frames), n)
    if not faces or faces[0]["hit"] < 0.35:
        raise cf.CamNotConfident("no stable face in the sampled frames (best track: "
                                 + (f"{faces[0]['n']}/{n} frames" if faces else "none") + "): cannot confirm a cam, pass cam_rect",
                                 {"faces": faces})
    vl, hl = persistent_steps(gray_frames)
    cands = candidates(faces, vl, hl, w_src, h_src, lambda r: outside_motion(gray_frames, r))
    info = {"faces": faces, "vlines": vl, "hlines": hl, "candidates": cands}
    if not cands:
        raise cf.CamNotConfident("faces found but none is stable enough to anchor a cam rectangle, pass cam_rect", info)
    best = cands[0]
    if best["confidence"] < min_conf:
        why = "; ".join(best["reasons"]) or f"confidence {best['confidence']} < {min_conf}"
        raise cf.CamNotConfident(f"no confident cam: best candidate {[round(v) for v in best['rect']]} ({why}): pass cam_rect", info)
    if len(cands) > 1 and cands[1]["confidence"] >= min_conf and cands[1]["confidence"] >= best["confidence"] - 0.1:
        raise cf.CamNotConfident("two different cam rectangles are equally plausible "
                                 f"({[round(v) for v in best['rect']]} and {[round(v) for v in cands[1]['rect']]}): pass cam_rect", info)
    res = cf.finish(gray_frames, faces, best, vl, hl)
    inside = res["cam"]["faces_in_rect"]
    res["method"] = "stable_overlay"
    res["face_hit_pct"] = round(100 * max(f["hit"] for f in inside), 1) if inside else 0.0
    res["pinned"] = {"edge_persist": best["edge_persist"], "outside_motion": best["outside_motion"]}
    return res
