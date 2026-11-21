"""Exclusion zones: stream overlays that must never appear in the gameplay panel.

Pure geometry (stdlib only). Zones are source pixels on a 1920x1080 frame and are scaled to the real
source size. A crop is a box (left, top, w, h) in source pixels.
"""
BASE_W, BASE_H = 1920, 1080
SAFE_PAD = 5          # px of clear space kept between a zone and the crop edge

# stream VOD id -> {zone name: (x0, y0, x1, y1)} on a 1920x1080 frame
VOD_ZONES = {
    "LHoHInbPkk0": {
        "cam": (0, 432, 392, 653),
        "chat": (0, 653, 392, 800),
        "counters": (1735, 530, 1920, 600),
        "timer": (1750, 130, 1900, 180),
    },
}


def zones_for(vod_id, src_w=BASE_W, src_h=BASE_H):
    """Zones for a known VOD scaled to the source size; {} when the VOD is unknown."""
    z = VOD_ZONES.get(vod_id)
    if not z:
        return {}
    sx, sy = src_w / BASE_W, src_h / BASE_H
    return {k: (x0 * sx, y0 * sy, x1 * sx, y1 * sy) for k, (x0, y0, x1, y1) in z.items()}


def _overlap(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def safe_x_range(zones, src_w, crop_top=0.0, crop_bottom=None, pad=SAFE_PAD):
    """(lo, hi) in source x that is clear of every zone for the crop's rows. Zones on the left half
    push lo right, zones on the right half pull hi left. 1080p with the VOD zones -> about 397..1730."""
    crop_bottom = BASE_H if crop_bottom is None else crop_bottom
    lo, hi = 0.0, float(src_w)
    for x0, y0, x1, y1 in zones.values():
        if y1 <= crop_top or y0 >= crop_bottom:
            continue
        if (x0 + x1) / 2 < src_w / 2:
            lo = max(lo, x1 + pad)
        else:
            hi = min(hi, x0 - pad)
    return lo, hi


def crop_left_bounds(zones, src_w, crop_w, crop_top=0.0, crop_bottom=None, pad=SAFE_PAD):
    """(min, max) for the crop's left edge, or None when the crop cannot fit between the zones."""
    lo, hi = safe_x_range(zones, src_w, crop_top, crop_bottom, pad)
    if hi - lo < crop_w:
        return None
    return lo, hi - crop_w


def clamp_x(x, bounds):
    lo, hi = bounds
    return max(lo, min(x, hi))


def zone_hits(box, zones):
    """Names of the zones a crop box (left, top, w, h) overlaps."""
    r = (box[0], box[1], box[0] + box[2], box[1] + box[3])
    return sorted(k for k, z in zones.items() if _overlap(r, z))


def overlay_gate(boxes, zones, sample_every=1):
    """(ok, detail, info): every sampled per-frame crop box must be clear of every zone.
    Checking the geometry of each frame is stricter than sampling finished pixels."""
    counts = {k: 0 for k in zones}
    n = 0
    for i, b in enumerate(boxes):
        if i % max(sample_every, 1):
            continue
        n += 1
        for k in zone_hits(b, zones):
            counts[k] += 1
    bad = {k: v for k, v in counts.items() if v}
    ok = n > 0 and not bad
    info = {"frames_checked": n, "hits": counts, "zones": {k: [round(v, 1) for v in z] for k, z in zones.items()}}
    detail = "no overlay in the gameplay panel" if ok else ("no frames checked" if n == 0 else
                                                           "overlay in panel: " + ", ".join(f"{k} x{v}" for k, v in bad.items()))
    return ok, detail, info


def subject_inside(subject_x, crop_left, crop_w, margin=0.0):
    """True when the subject's centre x is inside the crop, `margin` px away from either edge."""
    return crop_left + margin <= subject_x <= crop_left + crop_w - margin


def cut_stretches(subject_xs, fps, crop_w, bounds, margin=0.0, min_len=0.0):
    """[(t0, t1)] where the clamped crop would cut the subject (subject_xs: per-frame centre x or None).
    Those stretches are trimmed or skipped. Frames without a detection never count as cut."""
    lo, hi = bounds
    out, start = [], None
    for i, x in enumerate(subject_xs):
        bad = x is not None and not subject_inside(x, clamp_x(x - crop_w / 2, bounds), crop_w, margin)
        if bad and start is None:
            start = i
        if not bad and start is not None:
            out.append((start / fps, i / fps))
            start = None
    if start is not None:
        out.append((start / fps, len(subject_xs) / fps))
    return [(a, b) for a, b in out if b - a >= min_len]
