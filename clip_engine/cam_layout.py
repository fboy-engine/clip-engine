"""Where the facecam sits, and how far the 9:16 crop may travel to stay clear of it."""
import numpy as np

CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")


def norm_corner(s):
    s = (s or "").strip().lower().replace("_", "-").replace(" ", "-")
    return s if s in CORNERS else None


def static_rect(frames, corner, max_std=0.012, max_frac=0.45):
    """Bounding box (x0, y0, x1, y1) as 0..1 fractions of the low-motion block that touches
    `corner`, found on a gray frame stack (n, h, w). A facecam overlay barely changes while the
    game does. None when nothing static touches that corner."""
    corner = norm_corner(corner)
    f = np.asarray(frames, dtype=np.float32) / 255.0
    if corner is None or len(f) < 4:
        return None
    std = f.std(axis=0)
    h, w = std.shape
    static = std < max_std
    sy = 0 if corner.startswith("top") else h - 1
    sx = 0 if corner.endswith("left") else w - 1
    if not static[sy, sx]:
        return None
    seen, stack = np.zeros_like(static), [(sy, sx)]
    while stack:
        y, x = stack.pop()
        if not (0 <= y < h and 0 <= x < w) or seen[y, x] or not static[y, x]:
            continue
        seen[y, x] = True
        stack += [(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)]
    ys, xs = np.where(seen)
    r = ((xs.min()) / w, (ys.min()) / h, (xs.max() + 1) / w, (ys.max() + 1) / h)
    if (r[2] - r[0]) * (r[3] - r[1]) > max_frac or (r[2] - r[0]) < 0.08 or (r[3] - r[1]) < 0.08:
        return None
    return tuple(round(float(v), 3) for v in r)


def crop_bounds(rect, src_w, crop_w, pad=0.01):
    """(min_x, max_x) in pixels for the crop's left edge so the crop stays clear of a facecam
    on the left or right of the frame. Falls back to the full range when that is impossible."""
    lo, hi = 0.0, float(src_w - crop_w)
    if rect:
        x0, _, x1, _ = rect
        if x0 <= 0.02 and (x1 + pad) * src_w <= hi:        # cam hugs the left edge
            lo = (x1 + pad) * src_w
        elif x1 >= 0.98 and (x0 - pad) * src_w - crop_w >= lo:   # cam hugs the right edge
            hi = (x0 - pad) * src_w - crop_w
    return lo, hi


def visible_in_crop(rect, crop_x, crop_w, src_w):
    if not rect:
        return False
    return rect[0] * src_w < crop_x + crop_w and rect[2] * src_w > crop_x
