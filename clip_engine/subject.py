"""Which on-screen person is the subject, frame by frame, and is it inside the panel?

Pure numpy. Detections come from vision.detect_people (normalised boxes x0, y0, x1, y1, score);
this module chooses one per sample, holds through misses and reports coverage honestly.
"""
import numpy as np


def pick(boxes, prev_cx):
    """Largest detection, preferring the one nearest the previous subject position."""
    if not boxes:
        return None

    def key(b):
        cx = (b[0] + b[2]) / 2
        area = (b[2] - b[0]) * (b[3] - b[1])
        d = abs(cx - prev_cx) if prev_cx is not None else 0.0
        return d - 4.0 * area - 0.05 * b[4]
    return min(boxes, key=key)


def build(dets, motion_cx=None, hold=4):
    """dets: per sample, a list of normalised boxes. Returns
    {cx: [0..1], tracked: [bool], box: [box | None]}. A miss holds the last position for `hold`
    samples, then drifts toward the motion centroid; those samples stay flagged untracked."""
    cx, tracked, boxes = [], [], []
    prev, miss = None, 0
    for i, d in enumerate(dets):
        b = pick(d, prev)
        if b is not None:
            prev, miss = (b[0] + b[2]) / 2, 0
            cx.append(prev)
            tracked.append(True)
            boxes.append(b)
            continue
        miss += 1
        m = motion_cx[i] if motion_cx is not None and i < len(motion_cx) else 0.5
        if prev is None:
            v = m
        elif miss <= hold:
            v = prev
        else:
            v = 0.5 * prev + 0.5 * m
        cx.append(v)
        tracked.append(False)
        boxes.append(None)
    first = next((i for i, t in enumerate(tracked) if t), None)
    if first:
        for i in range(first):
            cx[i] = cx[first]
    return {"cx": cx, "tracked": tracked, "box": boxes}


STATIC_ABS = 1.5            # mean gray step (0..255) under a box below which nothing moves there
STATIC_REL = 0.5            # ... or below this share of the motion of the playable area
STATIC_MIN_HITS = 5         # a cluster needs this many detections before it can be called static
STATIC_SPREAD = 0.012       # std of the box centre x (fraction of the frame width) of a static cluster
MOTION_MIN = 0.8            # mean gray step of the playable area below which there is no motion mass to follow
MOTION_SPREAD_MAX = 0.6     # the middle 70% of the motion mass must fit in this share of the playable width (uniform = 0.7)


def playable_mask(h, w, masks):
    """(h, w) bool: True where the gameplay panel may look (outside the cam, chat and side-panel zones)."""
    m = np.ones((h, w), bool)
    for x0, y0, x1, y1 in masks or []:
        m[int(y0 * h):int(np.ceil(y1 * h)), int(x0 * w):int(np.ceil(x1 * w))] = False
    return m


def step_motion(gray, i):
    """Per-pixel |frame i - neighbour|, the neighbours i-1 and i+1 averaged. gray: (n, h, w) float."""
    ds = [np.abs(gray[i] - gray[j]) for j in (i - 1, i + 1) if 0 <= j < len(gray)]
    return np.mean(ds, axis=0) if ds else np.zeros_like(gray[0])


def drop_static(dets, gray, play):
    """Remove detections that sit in the same place in many samples with (almost) no motion under them: overlay art,
    HUD portraits and statues are not the player character. Returns (dets without them, [cluster info])."""
    gray = np.asarray(gray, dtype=np.float32)
    n = len(dets)
    if n == 0 or len(gray) < 3 or not play.any():
        return dets, []
    h, w = gray.shape[1:]
    steps = [step_motion(gray, i) for i in range(min(n, len(gray)))]
    base = float(np.median([st[play].mean() for st in steps]))
    items = []
    for i, d in enumerate(dets[:len(steps)]):
        for k, b in enumerate(d):
            x0, y0 = int(b[0] * w), int(b[1] * h)
            x1, y1 = max(x0 + 1, int(b[2] * w)), max(y0 + 1, int(b[3] * h))
            items.append((i, k, (b[0] + b[2]) / 2, b[2] - b[0], float(steps[i][y0:y1, x0:x1].mean())))
    clusters = []
    for it in items:
        for c in clusters:
            if abs(c["cx"] - it[2]) <= 0.03 and abs(c["bw"] - it[3]) <= 0.25 * max(c["bw"], 1e-6):
                c["m"].append(it)
                c["cx"] = float(np.mean([m[2] for m in c["m"]]))
                break
        else:
            clusters.append({"cx": it[2], "bw": it[3], "m": [it]})
    drop, info = set(), []
    for c in clusters:
        ms = c["m"]
        if len(ms) < STATIC_MIN_HITS:
            continue
        spread = float(np.std([m[2] for m in ms]))
        mot = float(np.median([m[4] for m in ms]))
        static = spread <= STATIC_SPREAD and mot < max(STATIC_ABS, STATIC_REL * base)
        info.append({"cx": round(c["cx"], 3), "hits": len(ms), "spread": round(spread, 4), "box_motion": round(mot, 2),
                     "playable_motion": round(base, 2), "static": bool(static)})
        if static:
            drop.update((m[0], m[1]) for m in ms)
    return [[b for k, b in enumerate(d) if (i, k) not in drop] for i, d in enumerate(dets)], info


def motion_box(step, play):
    """Box (x0, y0, x1, y1, 0.0) in fractions around the middle 70% of the motion mass inside the playable area, or None
    when nothing moves or the motion is spread over most of the panel width (a cut or a global change is not a subject)."""
    d = np.where(play, step, 0.0)
    tot = float(d.sum())
    h, w = d.shape
    if tot <= 0 or float(step[play].mean()) < MOTION_MIN:
        return None
    cx = np.cumsum(d.sum(axis=0)) / tot
    cy = np.cumsum(d.sum(axis=1)) / tot
    x0, x1 = np.searchsorted(cx, 0.15) / w, (np.searchsorted(cx, 0.85) + 1) / w
    y0, y1 = np.searchsorted(cy, 0.15) / h, (np.searchsorted(cy, 0.85) + 1) / h
    play_w = float(play.any(axis=0).mean())
    if (x1 - x0) > MOTION_SPREAD_MAX * play_w:
        return None
    return (float(x0), float(y0), float(min(x1, 1.0)), float(min(y1, 1.0)), 0.0)


def build_tracked(dets, gray, masks, hold=4):
    """The subject per sample. Live person detections only (static ones dropped), masked zones ignored; a sample without a
    person takes the compact motion mass of the playable area when there is one. Returns build()'s dict plus
    source: per sample 'person' | 'motion' | 'none', and static: the clusters that were dropped."""
    gray = np.asarray(gray, dtype=np.float32)
    n = len(dets)
    if n == 0 or len(gray) < n:
        return {**build(dets), "source": ["none"] * n, "static": []}
    play = playable_mask(gray.shape[1], gray.shape[2], masks)
    live, static = drop_static(dets, gray, play)
    steps = [step_motion(gray, i) for i in range(n)]
    mcx = []
    for st in steps:
        d = np.where(play, st, 0.0)
        tot = float(d.sum())
        mcx.append(0.5 if tot <= 1e-3 * d.size else float((d.sum(axis=0) * (np.arange(d.shape[1]) + 0.5)).sum() / tot / d.shape[1]))
    sub = build(live, mcx, hold)
    source = []
    for i in range(n):
        if sub["tracked"][i]:
            source.append("person")
            continue
        mb = motion_box(steps[i], play)
        if mb is None:
            source.append("none")
            continue
        sub["box"][i], sub["tracked"][i] = mb, True
        sub["cx"][i] = (mb[0] + mb[2]) / 2
        source.append("motion")
    first = next((i for i, t in enumerate(sub["tracked"]) if t), None)
    if first:
        for i in range(first):
            if source[i] == "none":
                sub["cx"][i] = sub["cx"][first]
    sub["source"], sub["static"] = source, static
    return sub


def inside_stats(boxes, times, lefts_px, crop_w_px, src_w, margin=0.01):
    """Plan-level check: is each tracked subject box fully inside the crop at its time?
    lefts_px: crop left edge (source px) for each sample time. Returns a stats dict."""
    ok = cut = 0
    cut_times = []
    cws = np.broadcast_to(np.asarray(crop_w_px, dtype=float), (len(times),))
    for b, t, left, cw in zip(boxes, times, lefts_px, cws):
        if b is None:
            continue
        x0, x1 = b[0] * src_w, b[2] * src_w
        m = margin * cw
        if x0 >= left + m and x1 <= left + cw - m:
            ok += 1
        else:
            cut += 1
            cut_times.append(round(float(t), 2))
    seen = ok + cut
    n = len(times)
    return {"inside_pct": round(100.0 * ok / seen, 1) if seen else None,
            "tracked_pct": round(100.0 * seen / n, 1) if n else 0.0,
            "in_panel_pct": round(100.0 * ok / n, 1) if n else 0.0,      # sampled frames with the subject fully inside the crop
            "cut_times": cut_times, "samples": n}


def classify_output(dets, w, margin_px=3):
    """Grade-time check on the RENDERED panel: per sample 'ok' (a person fully inside),
    'cut' (people found but all touch a side edge) or 'unseen'. dets: pixel boxes in a
    panel of width w."""
    out = []
    for d in dets:
        if not d:
            out.append("unseen")
        elif any(b[0] > margin_px and b[2] < w - margin_px for b in d):
            out.append("ok")
        else:
            out.append("cut")
    return out


def summarize_output(labels, times):
    ok, cut = labels.count("ok"), labels.count("cut")
    seen = ok + cut
    return {"inside_pct": round(100.0 * ok / seen, 1) if seen else None,
            "detected_pct": round(100.0 * seen / len(labels), 1) if labels else 0.0,
            "cut_times": [round(float(t), 2) for t, l in zip(times, labels) if l == "cut"],
            "unseen_times": [round(float(t), 2) for t, l in zip(times, labels) if l == "unseen"],
            "samples": len(labels)}
