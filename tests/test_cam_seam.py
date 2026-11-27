import numpy as np
from clip_engine.camfind import trim_cam_seam


def _frames(strip_w, n=4):
    out = []
    rng = np.random.default_rng(1)
    for _ in range(n):
        g = np.full((300, 600), 40.0, np.float32)
        g[:, :500 - strip_w] += np.linspace(0, 8, 500 - strip_w)
        if strip_w:
            g[:, 500 - strip_w:500] = 120.0 + 25.0 * np.sin(len(out) * 1.7 + np.arange(strip_w)[None, :])
        g += rng.normal(0, 1, g.shape)
        out.append(g)
    return out


def test_seam_strip_is_cut():
    r = trim_cam_seam((100.0, 0.0, 500.0, 300.0), _frames(30))
    assert 465 <= r[2] <= 471


def test_clean_cam_is_untouched():
    r = trim_cam_seam((100.0, 0.0, 500.0, 300.0), _frames(0))
    assert r[2] == 500.0
