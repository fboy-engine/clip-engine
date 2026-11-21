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
