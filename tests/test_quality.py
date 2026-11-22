import unittest

import numpy as np

from clip_engine import quality as q


def pattern(h=96, w=128, seed=1):
    r = np.random.RandomState(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = 128 + 60 * np.sin(x / 9.0) * np.cos(y / 7.0) + r.normal(0, 3, (h, w))
    return np.clip(np.stack([base, base * 0.9, base * 0.8], -1), 0, 255).astype(np.uint8)


def blocky(img, b=8, amp=14):
    """Add a per-block DC offset: what a low-bitrate encode does."""
    out = img.astype(np.int32).copy()
    r = np.random.RandomState(5)
    for y in range(0, img.shape[0], b):
        for x in range(0, img.shape[1], b):
            out[y:y + b, x:x + b] += int(r.randint(-amp, amp + 1))
    return np.clip(out, 0, 255).astype(np.uint8)


class Metrics(unittest.TestCase):
    def test_psnr_ssim_identity(self):
        a = pattern()
        self.assertEqual(q.psnr(a, a), 99.0)
        self.assertAlmostEqual(q.ssim(a, a), 1.0, places=6)

    def test_psnr_ssim_drop_with_noise(self):
        a = pattern()
        n = np.clip(a.astype(int) + np.random.RandomState(2).normal(0, 12, a.shape), 0, 255).astype(np.uint8)
        self.assertLess(q.psnr(a, n), 35)
        self.assertLess(q.ssim(a, n), 0.95)
        n2 = np.clip(a.astype(int) + np.random.RandomState(2).normal(0, 3, a.shape), 0, 255).astype(np.uint8)
        self.assertGreater(q.psnr(a, n2), q.psnr(a, n))
        self.assertGreater(q.ssim(a, n2), q.ssim(a, n))

    def test_blockiness_detects_the_grid(self):
        a = pattern()
        self.assertLess(q.blockiness(a, 8), 1.15)
        self.assertGreater(q.blockiness(blocky(a), 8), 1.6)

    def test_blockiness_respects_grid_and_offset(self):
        a = blocky(pattern())
        shifted = a[3:, 5:]                                   # grid now starts at col 3, row 5 (8-5, 8-3)
        self.assertGreater(q.blockiness(shifted, 8, ox=3, oy=5), 1.6)
        self.assertLess(q.blockiness(shifted, 8, ox=0, oy=0), 1.3)

    def test_edge_strength_follows_sharpness(self):
        a = pattern()
        step = np.zeros((64, 64, 3), np.uint8)
        step[:, 32:] = 200
        soft = np.zeros_like(step)
        soft[:, 28:36] = np.linspace(0, 200, 8).astype(np.uint8)[None, :, None]
        soft[:, 36:] = 200
        self.assertGreater(q.edge_strength(step, 99), q.edge_strength(soft, 99))
        self.assertGreater(q.mean_gradient(a), 0)

    def test_gray_input(self):
        g = pattern()[..., 0]
        self.assertAlmostEqual(q.ssim(g, g), 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
