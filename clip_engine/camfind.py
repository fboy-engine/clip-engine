"""A3: generic face-cam and overlay detector (any streamer, any cam size or position).

Evidence, never a guess. A cam is a STABLE FACE inside a RECTANGLE whose borders are long straight edges that persist across the
sampled frames (or run to the frame edge). A chat column is a strip at a frame edge whose rows repeat at a text-line pitch. When the
evidence is not enough, detect() raises CamNotConfident with the reasons and the caller must be given a cam_rect instead.
The decision code works on plain gray uint8 stacks (numpy + OpenCV), so it is testable without ffmpeg or a video file.
"""
import shutil
import subprocess

import numpy as np

from . import layout

MIN_CONFIDENCE = 0.6
N_SAMPLES = 24
CASCADES = ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml")


class CamNotConfident(ValueError):
    """The cam (or an overlay) could not be established from the frames. .info holds the evidence."""

    def __init__(self, message, info=None):
        super().__init__(message)
        self.info = info or {}


def _cv2():
    try:
        import cv2
        return cv2
    except ImportError as e:        # pragma: no cover
        raise RuntimeError('camfind needs opencv: pip install "opencv-python-headless>=4.9,<5"') from e


# ---- sampling -----------------------------------------------------------------------------------

def sample_times(start, dur, n=N_SAMPLES):
    return [round(start + (i + 0.5) * dur / n, 3) for i in range(n)]


def _frame_ffmpeg(media, t, w, h, color):
    pix = "bgr24" if color else "gray"
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", media, "-frames:v", "1", "-f", "rawvideo",
                        "-pix_fmt", pix, "-"], capture_output=True)
    need = w * h * (3 if color else 1)
    if len(p.stdout) != need:
        return None
    a = np.frombuffer(p.stdout, np.uint8)
    return a.reshape(h, w, 3) if color else a.reshape(h, w)


def _frame_cv2(cap, t, color):
    cv2 = _cv2()
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, f = cap.read()
    if not ok:
        return None
    return f if color else cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)


def grab(media, times, w, h, color=False):
    """Full-resolution frames at `times` (gray unless color). ffmpeg when present, else OpenCV. Missing frames are dropped."""
    out = []
    if shutil.which("ffmpeg"):
        out = [_frame_ffmpeg(media, t, w, h, color) for t in times]
    else:
        cap = _cv2().VideoCapture(media)
        try:
            out = [_frame_cv2(cap, t, color) for t in times]
        finally:
            cap.release()
    return [f for f in out if f is not None]


# ---- faces --------------------------------------------------------------------------------------

def _nms(dets):
    keep = []
    for x, y, w in sorted(dets, key=lambda d: -d[2]):
        cx, cy = x + w / 2, y + w / 2
        if all(abs(cx - (k[0] + k[2] / 2)) > 0.5 * k[2] or abs(cy - (k[1] + k[2] / 2)) > 0.5 * k[2] for k in keep):
            keep.append((x, y, w))
    return keep


def haar_faces(gray_frames, min_frac=1 / 30.0):
    """[(frame_index, cx, cy, w)] with the two frontal Haar cascades (strict: minNeighbors 6)."""
    cv2 = _cv2()
    h = gray_frames[0].shape[0]
    ms = max(24, int(h * min_frac))
    cs = [cv2.CascadeClassifier(cv2.data.haarcascades + n) for n in CASCADES]
    out = []
    for i, g in enumerate(gray_frames):
        e = cv2.equalizeHist(g)
        dets = [(int(x), int(y), int(w)) for c in cs for (x, y, w, _h) in c.detectMultiScale(e, 1.1, 6, minSize=(ms, ms))]
        out += [(i, x + w / 2, y + w / 2, float(w)) for x, y, w in _nms(dets)]
    return out


def cluster_faces(obs, n_frames):
    """Group detections that sit at the same place with the same size into tracks.
    -> [{cx, cy, w, hit (share of frames with a hit), spread (centre wobble / face width), n}] most frequent first."""
    cl = []
    for i, cx, cy, w in sorted(obs):
        for c in cl:
            m = c["pts"]
            mw = float(np.median([p[3] for p in m]))
            if (abs(cx - np.median([p[1] for p in m])) < 0.6 * mw and abs(cy - np.median([p[2] for p in m])) < 0.6 * mw
                    and 0.6 < w / mw < 1.6):
                m.append((i, cx, cy, w))
                break
        else:
            cl.append({"pts": [(i, cx, cy, w)]})
    out = []
    for c in cl:
        p = c["pts"]
        fw = float(np.median([q[3] for q in p]))
        cx, cy = float(np.median([q[1] for q in p])), float(np.median([q[2] for q in p]))
        spread = max(float(np.median(np.abs([q[1] - cx for q in p]))), float(np.median(np.abs([q[2] - cy for q in p])))) / fw
        out.append({"cx": round(cx, 1), "cy": round(cy, 1), "w": round(fw, 1), "n": len({q[0] for q in p}),
                    "hit": round(len({q[0] for q in p}) / max(n_frames, 1), 3), "spread": round(spread, 3)})
    return sorted(out, key=lambda c: -c["n"])


# ---- persistent straight borders ---------------------------------------------------------------

def _merge(segs, key, lo, hi, gap):
    """Merge collinear segments (same `key` within 3 px, gap <= `gap`). segs: dicts."""
    out = []
    for s in sorted(segs, key=lambda s: (s[key], s[lo])):
        for o in out:
            if abs(o[key] - s[key]) <= 3 and s[lo] - o[hi] <= gap:
                o[hi] = max(o[hi], s[hi])
                break
        else:
            out.append(dict(s))
    return out


def persistent_lines(gray_frames, thr=60, frac=0.85, minlen_frac=0.08):
    """Long straight edges present in >= `frac` of the frames. -> (vlines [{x, y0, y1}], hlines [{y, x0, x1}]).
    Static overlay borders survive; game content moves and does not."""
    cv2 = _cv2()
    n, (h, w) = len(gray_frames), gray_frames[0].shape
    vc, hc = np.zeros((h, w), np.uint8), np.zeros((h, w), np.uint8)
    for g in gray_frames:
        vc += (np.abs(cv2.Sobel(g, cv2.CV_16S, 1, 0, ksize=3)) > thr).astype(np.uint8)
        hc += (np.abs(cv2.Sobel(g, cv2.CV_16S, 0, 1, ksize=3)) > thr).astype(np.uint8)
    need = int(np.ceil(frac * n))
    res = []
    for acc, vertical in ((vc, True), (hc, False)):
        m = (acc >= need).astype(np.uint8)
        ml = int(minlen_frac * (h if vertical else w))
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, ml) if vertical else (ml, 1))
        nl, _, st, _ = cv2.connectedComponentsWithStats(cv2.morphologyEx(m, cv2.MORPH_OPEN, k), connectivity=8)
        if vertical:
            segs = [{"x": float(x + cw / 2), "y0": float(y), "y1": float(y + ch)} for x, y, cw, ch, _a in st[1:nl]]
            res.append(_merge(segs, "x", "y0", "y1", 0.1 * h))
        else:
            segs = [{"y": float(y + ch / 2), "x0": float(x), "x1": float(x + cw)} for x, y, cw, ch, _a in st[1:nl]]
            res.append(_merge(segs, "y", "x0", "x1", 0.1 * w))
    return res[0], res[1]


# ---- the cam ------------------------------------------------------------------------------------

def _iou(a, b):
    iw, ih = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    i = iw * ih
    return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i)


def _grow(f, vl, hl, w_src, h_src):
    """Rectangle around face track `f`: the nearest persistent border on each side, or the frame edge. -> (rect, sides)."""
    cx, cy, fw = f["cx"], f["cy"], f["w"]
    tol = 0.03 * h_src
    left = [l for l in vl if l["x"] < cx - 0.6 * fw and l["y0"] - tol <= cy <= l["y1"] + tol and cx - l["x"] <= 0.5 * w_src]
    right = [l for l in vl if l["x"] > cx + 0.6 * fw and l["y0"] - tol <= cy <= l["y1"] + tol and l["x"] - cx <= 0.5 * w_src]
    L = max(left, key=lambda l: l["x"]) if left else None
    R = min(right, key=lambda l: l["x"]) if right else None
    x0, x1 = (L["x"] if L else 0.0), (R["x"] if R else float(w_src))

    def cover(l):          # share of the rectangle's width this horizontal line really covers (gaps hide behind the face)
        return max(0.0, min(l["x1"], x1) - max(l["x0"], x0)) / max(x1 - x0, 1.0)

    up = [l for l in hl if l["y"] < cy - 0.6 * fw and cover(l) >= 0.45 and cy - l["y"] <= 0.6 * h_src]
    dn = [l for l in hl if l["y"] > cy + 0.6 * fw and cover(l) >= 0.45 and l["y"] - cy <= 0.6 * h_src]
    T = max(up, key=lambda l: l["y"]) if up else None
    B = min(dn, key=lambda l: l["y"]) if dn else None
    y0, y1 = (T["y"] if T else 0.0), (B["y"] if B else float(h_src))
    sides = {"left": "line" if L else "edge", "right": "line" if R else "edge",
             "top": "line" if T else "edge", "bottom": "line" if B else "edge"}
    return (x0, y0, x1, y1), sides


def cam_candidates(faces, vl, hl, w_src, h_src, min_hit=0.35):
    """Scored cam rectangles, best first. Each: rect, sides, confidence, reasons (what is weak), face."""
    out = []
    for f in faces:
        if f["hit"] < min_hit:
            continue
        rect, sides = _grow(f, vl, hl, w_src, h_src)
        rw, rh = rect[2] - rect[0], rect[3] - rect[1]
        real = sum(v == "line" for v in sides.values())
        edge = sum(v == "edge" for v in sides.values())
        area = rw * rh / float(w_src * h_src)
        reasons = []
        if real < 1:
            reasons.append("no straight border found around the face (a face alone is not a cam)")
        if edge > 2:
            reasons.append(f"{edge} of 4 sides have no border and no frame edge to stop at")
        if not 0.012 <= area <= 0.42:
            reasons.append(f"rectangle covers {100 * area:.1f}% of the frame (a cam is 1.2-42%)")
        if f["w"] / max(rw, 1.0) > 0.6 or f["w"] / max(rw, 1.0) < 0.04:
            reasons.append(f"face is {100 * f['w'] / max(rw, 1.0):.0f}% of the rectangle width")
        if not 0.5 <= rw / max(rh, 1.0) <= 3.6:
            reasons.append(f"rectangle aspect {rw / max(rh, 1.0):.2f} is not a cam shape")
        hit, stab = min(1.0, f["hit"] / 0.8), max(0.0, 1.0 - f["spread"] / 0.5)
        sup = min(1.0, (real + 0.5 * edge) / 3.0) if real else 0.0
        size = 1.0 if 0.05 <= f["w"] / max(rw, 1.0) <= 0.5 else 0.5
        conf = 0.35 * hit + 0.15 * stab + 0.35 * sup + 0.15 * size
        if reasons:
            conf = min(conf, MIN_CONFIDENCE - 0.01)
        out.append({"rect": tuple(round(float(v), 1) for v in rect), "sides": sides, "confidence": round(conf, 3),
                    "reasons": reasons, "face": f, "area_pct": round(100 * area, 2)})
    out.sort(key=lambda c: -c["confidence"])
    dedup = []
    for c in out:
        if all(_iou(c["rect"], d["rect"]) < 0.5 for d in dedup):
            dedup.append(c)
    return dedup


# ---- chat ---------------------------------------------------------------------------------------

def _acf_peak(profile, lo, hi):
    e = profile - np.convolve(profile, np.ones(81) / 81, "same")
    ac = np.correlate(e, e, "full")[len(e) - 1:]
    if len(ac) <= lo or ac[0] <= 1e-9:
        return 0, 0.0
    ac = ac / ac[0]
    hi = min(hi, len(ac) - 1)
    k = lo + int(np.argmax(ac[lo:hi + 1]))
    return k, float(ac[k])


def find_chat(gray_frames, cam_rect, w_src, h_src, thr=0.35, min_w=0.10, min_h=0.30):
    """Chat = a column at a frame edge whose rows repeat at a text-line pitch (2-7% of the height) in most frames.
    -> {side, rect, strength, pitch_px} or None."""
    n = len(gray_frames)
    bw = max(16, w_src // 60)
    lo, hi = max(8, int(0.02 * h_src)), int(0.07 * h_src)
    best = None
    for side in ("left", "right"):
        span = (0, 0.45 * w_src) if side == "left" else (0.55 * w_src, w_src)
        ya, yb = 0, h_src
        if cam_rect and cam_rect[0] < span[1] and cam_rect[2] > span[0]:       # keep the cam's rows out of the score
            free = [(0, cam_rect[1]), (cam_rect[3], h_src)]
            ya, yb = max(free, key=lambda r: r[1] - r[0])
        ya, yb = int(max(ya, 0.15 * h_src)) if ya == 0 else int(ya), int(yb)
        if yb - ya < min_h * h_src * 0.8:
            continue
        scores, lags = [], []
        for b in range(int(0.45 * w_src / bw)):
            xs = b * bw if side == "left" else w_src - (b + 1) * bw
            vals = []
            for g in gray_frames:
                prof = np.abs(np.diff(g[ya:yb, xs:xs + bw].astype(np.float32), axis=1)).mean(1)
                k, v = _acf_peak(prof, lo, hi)
                vals.append((v, k))
            scores.append(float(np.median([v for v, _ in vals])))
            lags.append(float(np.median([k for _, k in vals])))
        run, i = 0, 0
        while i < len(scores):
            if scores[i] >= thr:
                run, i = i + 1, i + 1
            elif i + 1 < len(scores) and scores[i + 1] >= thr:      # one weak band inside the column is fine
                i += 1
            else:
                break
        if run * bw < min_w * w_src:
            continue
        strength = float(np.median(scores[:run]))
        xw = run * bw
        rect_x = (0.0, float(xw)) if side == "left" else (float(w_src - xw), float(w_src))
        # vertical extent: rows with text energy
        cols = slice(0, xw) if side == "left" else slice(w_src - xw, w_src)
        e = np.mean([np.abs(np.diff(g[:, cols].astype(np.float32), axis=1)).mean(1) for g in gray_frames], axis=0)
        e = np.convolve(e, np.ones(21) / 21, "same")
        ref = float(np.median(e[ya:yb]))
        act = e > 0.35 * ref
        mid = (ya + yb) // 2
        t = mid
        while t > 0 and act[t - 1]:
            t -= 1
        b_ = mid
        while b_ < h_src - 1 and act[b_ + 1]:
            b_ += 1
        top, bot = (max(t, ya) if ya > 0 else t), (min(b_ + 1, yb) if yb < h_src else b_ + 1)
        if bot - top < min_h * h_src:
            continue
        cand = {"side": side, "rect": (rect_x[0], float(top), rect_x[1], float(bot)), "strength": round(strength, 3),
                "pitch_px": int(np.median(lags[:run]))}
        if best is None or cand["strength"] > best["strength"]:
            best = cand
    return best


# ---- put it together ---------------------------------------------------------------------------

def _frac(r, w_src, h_src):
    return [round(r[0] / w_src, 4), round(r[1] / h_src, 4), round(r[2] / w_src, 4), round(r[3] / h_src, 4)]


def detect(gray_frames, face_fn=None, min_conf=MIN_CONFIDENCE):
    """Cam rectangle + overlay zones from sampled gray frames. Raises CamNotConfident (with every reason) instead of guessing.
    -> {cam: {rect_px, rect_frac, confidence, face, sides, ...}, chat, zones {name: (x0, y0, x1, y1) px}, confidence, warnings}"""
    n = len(gray_frames)
    if n < 6:
        raise CamNotConfident(f"only {n} frames sampled: need at least 6 to tell an overlay from game content")
    h_src, w_src = gray_frames[0].shape
    faces = cluster_faces((face_fn or haar_faces)(gray_frames), n)
    if not faces or faces[0]["hit"] < 0.35:
        raise CamNotConfident("no stable face in the sampled frames (best track: "
                              + (f"{faces[0]['n']}/{n} frames" if faces else "none") + "): cannot establish a cam, pass cam_rect",
                              {"faces": faces})
    vl, hl = persistent_lines(gray_frames)
    cands = cam_candidates(faces, vl, hl, w_src, h_src)
    info = {"faces": faces, "vlines": vl, "hlines": hl, "candidates": cands}
    if not cands:
        raise CamNotConfident("faces found but none is stable enough to anchor a cam rectangle, pass cam_rect", info)
    best = cands[0]
    if best["confidence"] < min_conf:
        why = "; ".join(best["reasons"]) or f"confidence {best['confidence']} < {min_conf}"
        raise CamNotConfident(f"no confident cam: best candidate {[round(v) for v in best['rect']]} ({why}): pass cam_rect", info)
    if len(cands) > 1 and cands[1]["confidence"] >= min_conf and cands[1]["confidence"] >= best["confidence"] - 0.1:
        raise CamNotConfident("two different cam rectangles are equally plausible "
                              f"({[round(v) for v in best['rect']]} and {[round(v) for v in cands[1]['rect']]}): pass cam_rect", info)
    return finish(gray_frames, faces, best, vl, hl)


def finish(gray_frames, faces, best, vl, hl):
    """Chat/zones/result dict around the chosen cam candidate `best` (shared by detect() and stable_overlay.detect())."""
    n = len(gray_frames)
    h_src, w_src = gray_frames[0].shape
    rect = best["rect"]
    inside = [f for f in faces if rect[0] <= f["cx"] <= rect[2] and rect[1] <= f["cy"] <= rect[3]]
    chat = find_chat(gray_frames, rect, w_src, h_src)
    zones, warnings = {"cam": rect}, []
    if chat:
        zones["chat"] = chat["rect"]
        c, ch = rect, chat["rect"]
        same_side = (c[0] + c[2]) / 2 > w_src / 2 and (ch[0] + ch[2]) / 2 > w_src / 2
        same_side = same_side or ((c[0] + c[2]) / 2 < w_src / 2 and (ch[0] + ch[2]) / 2 < w_src / 2)
        if same_side and min(c[2], ch[2]) - max(c[0], ch[0]) >= 0.5 * min(c[2] - c[0], ch[2] - ch[0]):
            if (c[0] + c[2]) / 2 > w_src / 2:
                zones["side_panel"] = (min(c[0], ch[0]), 0.0, float(w_src), float(h_src))
            else:
                zones["side_panel"] = (0.0, 0.0, max(c[2], ch[2]), float(h_src))
    else:
        warnings.append("no chat column found (text-line pitch search); only the cam zone is excluded")
    warnings.append("floating counters/timers outside the cam/chat column are not detected")
    return {"width": w_src, "height": h_src, "frames": n,
            "cam": {"rect_px": [round(v) for v in rect], "rect_frac": _frac(rect, w_src, h_src), "confidence": best["confidence"],
                    "sides": best["sides"], "area_pct": best["area_pct"], "face": best["face"], "faces_in_rect": inside},
            "chat": chat, "zones": {k: tuple(round(float(v), 1) for v in z) for k, z in zones.items()},
            "confidence": best["confidence"], "warnings": warnings, "evidence": {"vlines": vl, "hlines": hl, "faces": faces}}


def cam_window(rect, face_cx, face_cy, out_w=1080):
    """The 2:1 window of the cam that fills the 1080x540 top block: the biggest 2:1 window inside the rectangle, the face at the
    layout's face row, never stretched. Downscale only when the window is already >= out_w wide, else the cam upscale chain runs."""
    x0, y0, x1, y1 = [float(v) for v in rect]
    w = min(x1 - x0, 2.0 * (y1 - y0))
    h = w / layout.CAM_ASPECT
    left = min(max(face_cx - w / 2, x0), x1 - w)
    top = min(max(face_cy - layout.FACE_ROW * h, y0), y1 - h)
    return {"box": [round(left, 1), round(top, 1), round(w, 1), round(h, 1)], "scale": round(out_w / w, 3),
            "mode": "downscale" if w >= out_w else "upscale"}


def draw_debug(color_frame, result, path):
    """Annotated frame: cam rectangle (green) with its confidence, faces (yellow), chat (cyan), side panel (magenta), persistent lines (red)."""
    cv2 = _cv2()
    img = color_frame.copy()
    ev = result.get("evidence", {})
    for l in ev.get("vlines", []):
        cv2.line(img, (int(l["x"]), int(l["y0"])), (int(l["x"]), int(l["y1"])), (0, 0, 255), 2)
    for l in ev.get("hlines", []):
        cv2.line(img, (int(l["x0"]), int(l["y"])), (int(l["x1"]), int(l["y"])), (0, 0, 255), 2)
    for f in ev.get("faces", []):
        a, b = (int(f["cx"] - f["w"] / 2), int(f["cy"] - f["w"] / 2)), (int(f["cx"] + f["w"] / 2), int(f["cy"] + f["w"] / 2))
        cv2.rectangle(img, a, b, (0, 255, 255), 2)
    colors = {"cam": (0, 220, 0), "chat": (255, 200, 0), "side_panel": (255, 0, 255)}
    for k, z in result["zones"].items():
        cv2.rectangle(img, (int(z[0]), int(z[1])), (int(z[2]) - 1, int(z[3]) - 1), colors.get(k, (255, 255, 255)), 4 if k != "side_panel" else 1)
        cv2.putText(img, k + (f" {result['cam']['confidence']}" if k == "cam" else ""), (int(z[0]) + 8, int(z[1]) + 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, colors.get(k, (255, 255, 255)), 2)
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return path


def detect_media(media, start, dur, w_src, h_src, n=N_SAMPLES, face_fn=None, debug_jpg=None):
    """Sample `n` frames of the window and run detect(). With `debug_jpg` the annotated middle frame is written."""
    times = sample_times(start, dur, n)
    gray = grab(media, times, w_src, h_src)
    try:
        res = detect(gray, face_fn=face_fn)
        res["method"] = "border_lines"
    except CamNotConfident as e1:           # no drawn border: fall back to the stable-overlay rule (same frames)
        from . import stable_overlay
        try:
            res = stable_overlay.detect(gray, face_fn=face_fn)
        except CamNotConfident as e2:
            raise CamNotConfident(f"{e1} | stable-overlay fallback: {e2}", {**e1.info, "stable_overlay": e2.info}) from e2
    if debug_jpg:
        col = grab(media, [times[len(times) // 2]], w_src, h_src, color=True)
        draw_debug(col[0] if col else np.dstack([gray[len(gray) // 2]] * 3), res, debug_jpg)
    return res


# ---- the gate on the RENDERED output ----------------------------------------------------------------

PANEL_Y0 = 540                     # the gameplay panel starts under the 1080x540 cam block
LOC_MIN_SCORE = 0.5                # normalised correlation needed to say "this output patch came from there"
LOC_TOL_PX = 12                    # source px of slack on a located box (matching runs at 1/4 resolution)


def locate_patch(src_gray, patch, scales, f=0.25):
    """Where in the SOURCE frame did an output patch come from? The patch was the source scaled up by one of `scales`; it is shrunk
    to the matching size and found by normalised cross-correlation on a 1/4 resolution copy (invariant to brightness/contrast grading).
    -> {score, scale, x, y, w, h} in source px of the best match (w, h = the patch's footprint in the source)."""
    cv2 = _cv2()
    small = cv2.resize(src_gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    best = {"score": -1.0}
    ph, pw = patch.shape[:2]
    for s in scales:
        tw, th = int(round(pw * f / s)), int(round(ph * f / s))
        if tw < 8 or th < 8 or tw > small.shape[1] or th > small.shape[0]:
            continue
        t = cv2.resize(patch, (tw, th), interpolation=cv2.INTER_AREA)
        if float(t.std()) < 2.0:           # a flat patch matches everything: no evidence
            continue
        res = cv2.matchTemplate(small, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx > best["score"]:
            best = {"score": round(float(mx), 3), "scale": round(float(s), 3), "x": loc[0] / f, "y": loc[1] / f,
                    "w": pw / s, "h": ph / s}
    return best


def _overlap_px(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def gate_frames(pairs, zones, cam_rect, panel_scales, cam_scales, min_score=LOC_MIN_SCORE, tol=LOC_TOL_PX):
    """pairs: [(source gray frame, output gray frame 1080x1920)]. Every output frame is traced back into the source:
    the gameplay panel must come from a place clear of every overlay zone, the cam block from inside the cam rectangle.
    -> {ok, reasons, frames: [...]} (a frame that cannot be traced counts as a failure: no evidence is not a pass)."""
    rows, reasons = [], []
    for k, (src, out) in enumerate(pairs):
        h_out = out.shape[0]
        panel = out[PANEL_Y0 + 160: h_out - 380]                # middle band: clear of the seam captions and the bottom lane
        off = 160.0                                              # rows of the panel above the matched band
        p = locate_patch(src, panel, panel_scales)
        row = {"frame": k, "panel": {kk: (round(v, 1) if isinstance(v, float) else v) for kk, v in p.items()}}
        if p["score"] < min_score:
            reasons.append(f"frame {k}: the gameplay panel could not be traced back into the source (score {p['score']} < {min_score})")
        else:
            top = p["y"] - off / p["scale"]
            box = (p["x"], top, p["x"] + p["w"], top + (h_out - PANEL_Y0) / p["scale"])
            shrunk = (box[0] + tol, box[1] + tol, box[2] - tol, box[3] - tol)
            hits = sorted(n for n, z in zones.items() if _overlap_px(shrunk, z) > 0)
            row["panel"]["x_range"] = [round(box[0]), round(box[2])]
            row["panel"]["zone_hits"] = hits
            if hits:
                reasons.append(f"frame {k}: the rendered gameplay panel shows source x {round(box[0])}-{round(box[2])}, "
                               f"which overlaps the {', '.join(hits)} zone")
        if cam_rect:
            c = locate_patch(src, out[:PANEL_Y0], cam_scales)
            row["cam"] = {kk: (round(v, 1) if isinstance(v, float) else v) for kk, v in c.items()}
            if c["score"] < min_score * 0.7:                   # the enhanced cam block matches a little less sharply
                reasons.append(f"frame {k}: the cam block could not be traced back into the source (score {c['score']})")
            else:
                inside = (c["x"] >= cam_rect[0] - tol and c["y"] >= cam_rect[1] - tol and c["x"] + c["w"] <= cam_rect[2] + tol
                          and c["y"] + c["h"] <= cam_rect[3] + tol)
                row["cam"]["inside_cam_rect"] = bool(inside)
                if not inside:
                    reasons.append(f"frame {k}: the cam block comes from source box {[round(c['x']), round(c['y']), round(c['x'] + c['w']), round(c['y'] + c['h'])]}, outside the cam rectangle")
        rows.append(row)
    return {"ok": bool(rows) and not reasons, "reasons": reasons if rows else ["no frames to check"], "frames": rows}


def rendered_gate(media, clip_path, start, dur, w_src, h_src, zones, cam_rect, panel_scales, cam_scales, k=5):
    """Decode k frames of the FINISHED clip and the same moments of the source, then gate_frames() them."""
    us = [round(dur * (i + 1) / (k + 1), 3) for i in range(k)]
    out = grab(clip_path, us, 1080, 1920)
    src = grab(media, [round(start + u, 3) for u in us], w_src, h_src)
    n = min(len(out), len(src))
    res = gate_frames(list(zip(src[:n], out[:n])), zones, cam_rect, panel_scales, cam_scales)
    res["times"] = us[:n]
    if n < k:
        res["ok"], res["reasons"] = False, res["reasons"] + [f"only {n} of {k} frames could be decoded from the clip"]
    return res
