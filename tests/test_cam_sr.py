import hashlib
import os
import tempfile
import unittest

import numpy as np

from clip_engine import cam_sr, srcinfo


def row(sec, psnr, ssim, blk, edge=30.0):
    return {"sec_per_frame": sec, "psnr": psnr, "ssim": ssim, "blockiness": blk, "edge": edge}


BASE = row(0.005, 28.0, 0.92, 1.00)


class Choose(unittest.TestCase):
    def test_fit_fps_tiers(self):
        self.assertEqual(cam_sr.fit_fps(0.05), 30)
        self.assertEqual(cam_sr.fit_fps(0.45), 24)       # 0.45*1.25*1080 = 607 <= 720 but not at 30 fps (759)
        self.assertIsNone(cam_sr.fit_fps(0.8))

    def test_best_ssim_that_fits_and_beats_lanczos_wins(self):
        r = {"lanczos": BASE, "fsrcnn": row(0.05, 28.5, 0.925, 0.98), "edsr": row(3.0, 29.0, 0.95, 0.9),
             "realesrgan": row(0.45, 28.9, 0.94, 0.95)}
        s = cam_sr.choose(r)
        self.assertEqual((s["method"], s["cam_fps"]), ("realesrgan", 24))
        self.assertIn("too slow", s["rejected"]["edsr"])

    def test_candidate_must_not_lose_to_lanczos(self):
        r = {"lanczos": BASE, "fsrcnn": row(0.05, 29.0, 0.91, 0.98)}          # worse SSIM
        s = cam_sr.choose(r)
        self.assertEqual(s["method"], "lanczos")
        self.assertIn("not better", s["rejected"]["fsrcnn"])

    def test_blockiness_regression_rejects(self):
        r = {"lanczos": BASE, "fsrcnn": row(0.05, 29.0, 0.93, 1.3)}
        self.assertEqual(cam_sr.choose(r)["method"], "lanczos")

    def test_error_candidates_are_reported_not_fatal(self):
        r = {"lanczos": BASE, "edsr": {"error": "ModelError: x"}}
        s = cam_sr.choose(r)
        self.assertEqual(s["method"], "lanczos")
        self.assertIn("unavailable", s["rejected"]["edsr"])


class Deblock(unittest.TestCase):
    def test_needs_gain_and_ssim(self):
        rows = {"none": {"blockiness": 1.30, "ssim": 1.0}, "a": {"blockiness": 1.05, "ssim": 0.97},
                "b": {"blockiness": 1.00, "ssim": 0.80}, "c": {"error": "x"}}
        self.assertEqual(cam_sr.choose_deblock(rows), "a")           # b smears (SSIM too low)

    def test_none_when_no_real_gain(self):
        rows = {"none": {"blockiness": 1.02, "ssim": 1.0}, "a": {"blockiness": 1.00, "ssim": 0.99}}
        self.assertEqual(cam_sr.choose_deblock(rows), "none")

    def test_no_pp_filter_in_the_candidates(self):
        self.assertNotIn("pp=", " ".join(cam_sr.DEBLOCK_VARIANTS.values()).replace("spp=", "").replace("pp7=", ""))


class Models(unittest.TestCase):
    def test_sha_is_enforced_and_cached(self):
        data = b"model-bytes"
        orig = dict(cam_sr.MODELS["fsrcnn"])
        cam_sr.MODELS["fsrcnn"] = {**orig, "sha256": hashlib.sha256(data).hexdigest()}
        try:
            d = tempfile.mkdtemp()
            calls = []
            p = cam_sr.ensure_model("fsrcnn", d, lambda u: calls.append(u) or data)
            self.assertEqual(open(p, "rb").read(), data)
            cam_sr.ensure_model("fsrcnn", d, lambda u: calls.append(u) or b"other")
            self.assertEqual(len(calls), 1)                          # second call used the verified file
            os.remove(p)
            with self.assertRaises(cam_sr.ModelError):
                cam_sr.ensure_model("fsrcnn", d, lambda u: b"tampered")
            with self.assertRaises(cam_sr.ModelError):
                cam_sr.ensure_model("fsrcnn", d, lambda u: (_ for _ in ()).throw(OSError("offline")))
        finally:
            cam_sr.MODELS["fsrcnn"] = orig

    def test_pinned_hashes_are_sha256(self):
        for m in cam_sr.MODELS.values():
            self.assertRegex(m["sha256"], r"^[0-9a-f]{64}$")


class Finish(unittest.TestCase):
    def test_grain_is_light_and_deterministic(self):
        img = np.full((60, 80, 3), 128, np.uint8)
        a, b = cam_sr.grain(img, 7), cam_sr.grain(img, 7)
        self.assertTrue(np.array_equal(a, b))
        self.assertFalse(np.array_equal(a, cam_sr.grain(img, 8)))
        d = a.astype(int) - 128
        self.assertLess(abs(float(d.mean())), 0.3)
        self.assertLess(float(d.std()), 1.6)
        self.assertTrue(np.all(d[..., 0] == d[..., 1]))              # luma grain: no colour speckle

    def test_chain_description(self):
        c = cam_sr.chain_description("deblock_weak", "fsrcnn", 30)
        self.assertEqual(c["deblock_vf"], "deblock=filter=weak:block=8")
        self.assertEqual(c["cam_fps"], 30)


class SourceInfo(unittest.TestCase):
    CACHED = {"height": 1080, "video_kbps": 4000}

    def test_better_formats(self):
        fm = [{"id": "a", "height": 1080, "tbr": 4200}, {"id": "b", "height": 1080, "tbr": 5200},
              {"id": "c", "height": 1440, "tbr": 9000}, {"id": "d", "height": 720, "tbr": 9000}]
        self.assertEqual([f["id"] for f in srcinfo.better_formats(self.CACHED, fm)], ["c", "b"])
        self.assertEqual(srcinfo.better_formats(self.CACHED, None), [])

    def test_info_line(self):
        s = srcinfo.info_line({"vcodec": "h264", "profile": "High", "pix_fmt": "yuv420p", "width": 1920, "height": 1080,
                               "fps": 60.0, "video_kbps": 5200, "container": "mov,mp4"})
        self.assertIn("1920x1080", s)
        self.assertIn("5200 kbps", s)


if __name__ == "__main__":
    unittest.main()
