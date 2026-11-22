"""Image-quality metrics for the cam block and the gameplay panel. numpy only (no OpenCV, no scipy).

All images are uint8 arrays, HxWx3 BGR or HxW gray. Lower blockiness is better; higher PSNR, SSIM and
edge strength are better.
"""
import numpy as np


def to_gray(img):
    a = np.asarray(img)
    if a.ndim == 2:
        return a.astype(np.float64)
    a = a.astype(np.float64)
    return 0.114 * a[..., 0] + 0.587 * a[..., 1] + 0.299 * a[..., 2]      # BGR luma


def psnr(a, b):
    """PSNR over all channels, dB. Identical images give 99.0 (finite, so tables and JSON stay sane)."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    mse = float(np.mean((a - b) ** 2))
    return 99.0 if mse < 1e-10 else float(10 * np.log10(255.0 ** 2 / mse))


def _box(x, k):
    """Mean over every k x k window (valid positions only), via cumulative sums."""
    c = np.cumsum(np.cumsum(np.pad(x, ((1, 0), (1, 0))), axis=0), axis=1)
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s / (k * k)


def ssim(a, b, k=7):
    """Mean SSIM of the luma planes (uniform k x k window, L=255)."""
    x, y = to_gray(a), to_gray(b)
    if min(x.shape) < k:
        return 1.0 if np.array_equal(x, y) else 0.0
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mx, my = _box(x, k), _box(y, k)
    vx, vy = _box(x * x, k) - mx * mx, _box(y * y, k) - my * my
    cxy = _box(x * y, k) - mx * my
    m = ((2 * mx * my + c1) * (2 * cxy + c2)) / ((mx * mx + my * my + c1) * (vx + vy + c2))
    return float(np.mean(m))


def blockiness(img, grid=8, ox=0, oy=0):
    """Blocking-artifact ratio: mean luma step across block borders divided by the mean step elsewhere.
    ~1.0 = no visible block grid, > 1 = blocks. grid is the border spacing in THIS image's pixels (a
    source 8 px transform grid seen after a 3x upscale is grid 24); ox/oy = column/row of the first border."""
    g = to_gray(img)
    h, w = g.shape
    if h < 2 * grid or w < 2 * grid:
        return 1.0
    dx = np.abs(np.diff(g, axis=1))                    # step between column j and j+1
    dy = np.abs(np.diff(g, axis=0))
    jx = (np.arange(w - 1) + 1 - ox) % grid == 0
    jy = (np.arange(h - 1) + 1 - oy) % grid == 0
    rx = dx[:, jx].mean() / (dx[:, ~jx].mean() + 1e-6)
    ry = dy[jy, :].mean() / (dy[~jy, :].mean() + 1e-6)
    return float((rx + ry) / 2)


def edge_strength(img, pct=95.0):
    """Noise-robust sharpness: the pct-th percentile of the gradient magnitude (strong edges only)."""
    g = to_gray(img)
    gx = np.abs(np.diff(g, axis=1))[:-1, :]
    gy = np.abs(np.diff(g, axis=0))[:, :-1]
    return float(np.percentile(np.hypot(gx, gy), pct))


def mean_gradient(img):
    """Mean gradient magnitude (counts fine texture and noise too)."""
    g = to_gray(img)
    gx = np.abs(np.diff(g, axis=1))[:-1, :]
    gy = np.abs(np.diff(g, axis=0))[:, :-1]
    return float(np.hypot(gx, gy).mean())
