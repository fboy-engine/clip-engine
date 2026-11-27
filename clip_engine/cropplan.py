"""9:16 crop path from subject positions: smoothed, no jitter, capped punch-in."""
from .config import PUNCH_TARGET, PUNCH_HARD_CAP


def smooth(xs, alpha=0.15, deadband=0.02):
    """Exponential smoothing with a deadband so the crop holds still on small motion."""
    out, cur = [], None
    for x in xs:
        if cur is None:
            cur = x
        elif abs(x - cur) > deadband:
            cur += alpha * (x - cur)
        out.append(cur)
    return out


def crop_window(cx, src_w, src_h, zoom=0.0):
    """(x, w, h) of a 9:16 crop centred on cx (0..1), clamped inside the frame.
    zoom is a punch-in fraction, hard-clamped to PUNCH_HARD_CAP."""
    zoom = max(0.0, min(zoom, PUNCH_HARD_CAP))
    h = src_h / (1.0 + zoom)
    w = h * 9 / 16
    x = max(0.0, min(cx * src_w - w / 2, src_w - w))
    return x, w, h


def punch_curve(n, peak=PUNCH_TARGET):
    """Slow ease-in to `peak`, never above the normal 8% target."""
    peak = min(peak, PUNCH_TARGET)
    return [peak * (i / max(n - 1, 1)) ** 2 for i in range(n)]


def keep_in_frame(cxs, boxes, crop_w, src_w, margin=0.012):
    """Pull the smoothed crop centres so the whole subject box stays inside the crop whenever it fits.
    Smoothing lag and the dead zone eat the slack of a wide subject (a 650 px box in an 845 px crop has ~100 px
    each side), so the planner cut the subject at 50-75% of the samples although it was detected every time.
    cxs: crop centres (0..1), boxes: normalised [x0, y0, x1, y1, ...] or None, crop_w: narrowest crop width in px."""
    out, m = [], margin * crop_w
    for c, b in zip(cxs, boxes):
        if b is None:
            out.append(c)
            continue
        x0, x1 = b[0] * src_w, b[2] * src_w
        lo, hi = x1 - crop_w / 2.0 + m, x0 + crop_w / 2.0 - m
        if lo > hi:                      # wider than the crop: it cannot fit, leave the centre alone
            out.append(c)
        else:
            out.append(min(max(c * src_w, lo), hi) / src_w)
    return out


def dark_exempt(sources, boxes, times, cut_times, lumas, dark_luma=50.0):
    """Indices of samples the tracking gate may skip: the scene is dark (mean luma below `dark_luma`), the
    subject was not really detected (source none or motion) and it is not inside the crop anyway.
    A character that no detector can see in a dark frame is not a reason to refuse the clip."""
    cut = set(round(float(t), 2) for t in cut_times)
    out = []
    for i, (s, b, t, lu) in enumerate(zip(sources, boxes, times, lumas)):
        notok = b is None or round(float(t), 2) in cut
        if s in ("none", "motion") and lu < dark_luma and notok:
            out.append(i)
    return out
