"""Subject tracking for the 9:16 crop: where the action is, smoothed, as an ffmpeg expression."""
import numpy as np

from .cropplan import smooth, crop_window


def centroid_x(frames, mask_rect=None):
    """Per-frame x (0..1) of where the picture changes most. `mask_rect` (x0,y0,x1,y1 fractions)
    is ignored so a moving streamer in the facecam does not drag the crop. 0.5 when nothing moves."""
    f = np.asarray(frames, dtype=np.float32)
    if len(f) < 2:
        return [0.5]
    d = np.abs(np.diff(f, axis=0))
    n, h, w = d.shape
    if mask_rect:
        x0, y0, x1, y1 = mask_rect
        d[:, int(y0 * h):int(np.ceil(y1 * h)), int(x0 * w):int(np.ceil(x1 * w))] = 0
    cols = d.sum(axis=1)
    tot = cols.sum(axis=1)
    xs = (np.arange(w) + 0.5) / w
    cx = np.where(tot > 1e-3 * h * w, (cols * xs).sum(axis=1) / np.maximum(tot, 1e-9), 0.5)
    return [0.5] + cx.tolist()


def plan(frames, fps, src_w, src_h, mask_rect=None, bounds=None, step=0.5):
    """Crop path: [(t_seconds, x_pixels)] every `step` s plus the crop size. Smoothed with a
    deadband so the crop holds still on small motion; x clamped inside `bounds`."""
    cx = smooth(centroid_x(frames, mask_rect))
    _, cw, ch = crop_window(0.5, src_w, src_h)
    cw, ch = int(round(cw / 2) * 2), int(round(ch / 2) * 2)
    lo, hi = bounds if bounds else (0.0, float(src_w - cw))
    pts, n = [], int(len(cx) / fps / step)
    for i in range(max(n, 1) + 1):
        j = min(int(round(i * step * fps)), len(cx) - 1)
        x, _, _ = crop_window(cx[j], src_w, src_h)
        pts.append((round(i * step, 3), round(max(lo, min(x, hi)), 1)))
    return {"points": pts, "crop_w": cw, "crop_h": ch}


def lerp_expr(points, var="t"):
    """Piecewise-linear ffmpeg expression through (t, value) points; holds the ends."""
    if len(points) == 1:
        return f"{points[0][1]:g}"
    expr = f"{points[-1][1]:g}"
    for (t0, v0), (t1, v1) in reversed(list(zip(points, points[1:]))):
        seg = f"({v0:g}+({v1 - v0:g})*({var}-{t0:g})/{t1 - t0:g})"
        expr = f"if(lt({var},{t1:g}),{seg},{expr})"
    return expr
