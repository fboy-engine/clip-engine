import os
import tempfile
import unittest

import numpy as np

from clip_engine import cam_enhance, cam_sr


def fake4(img):
    """Stands in for the model: nearest 4x, so a tiled run must equal a whole run exactly."""
    return np.repeat(np.repeat(img, 4, axis=0), 4, axis=1)


class TiledSrTest(unittest.TestCase):
    def test_tiles_equal_the_whole_picture(self):
        rng = np.random.RandomState(3)
        img = rng.randint(0, 255, (203, 517, 3), dtype=np.uint8)
        whole = fake4(img)
        tiled = cam_sr.tiled_run4(fake4, img, tile=96, ov=8)
        self.assertEqual(tiled.shape, whole.shape)
        self.assertTrue(np.array_equal(tiled, whole))

    def test_each_call_stays_bounded(self):
        seen = []

        def fn(x):
            seen.append(x.shape[0] * x.shape[1])
            return fake4(x)
        cam_sr.tiled_run4(fn, np.zeros((700, 900, 3), np.uint8), tile=320, ov=16)
        self.assertLessEqual(max(seen), (320 + 32) ** 2)
        self.assertGreater(len(seen), 1)

    def test_normal_roi_is_one_call(self):
        calls = []
        up = cam_sr.OnnxSR()
        up._run4_whole = lambda im: calls.append(im.shape) or fake4(im)
        up.run4(np.zeros((282, 534, 3), np.uint8))
        self.assertEqual(len(calls), 1)
        up.run4(np.zeros((900, 1200, 3), np.uint8))
        self.assertGreater(len(calls), 2)


try:
    import cv2  # noqa: F401
    HAVE_CV2 = True
except ImportError:                       # e.g. no libGL on a bare box: CI has it
    HAVE_CV2 = False


@unittest.skipUnless(HAVE_CV2, "needs cv2")
class SrFallbackTest(unittest.TestCase):
    def stream(self, sr_limit):
        s = cam_enhance.CamStream.__new__(cam_enhance.CamStream)
        s.clean = {k: np.full((20, 30, 3), 40 + k, np.uint8) for k in range(4)}
        s.mixed, s.lan4 = {}, {}
        s.sr_sec, s.sr_frames, s.fallback, s.sr_limit, s.blend = 0.0, 0, None, sr_limit, 0.5
        s.logs = []
        s.log = s.logs.append
        s._read_to = lambda k: None
        calls = []

        class Up:
            def run4(self, im):
                calls.append(1)
                return fake4(im)
        s.up = Up()
        s.calls = calls
        return s

    def test_over_budget_switches_to_lanczos_and_says_so(self):
        s = self.stream(sr_limit=10.0)
        s._sr(0)
        self.assertEqual(len(s.calls), 1)
        s.sr_sec = 11.0                       # the budget is gone
        s._sr(1)
        s._sr(2)
        self.assertEqual(len(s.calls), 1)     # no more model calls
        self.assertEqual(s.fallback["at_frame"], 1)
        self.assertEqual(s.mixed[2].shape, (80, 120, 3))
        self.assertTrue(any("plain lanczos" in m for m in s.logs))

    def test_in_budget_never_falls_back(self):
        s = self.stream(sr_limit=1000.0)
        for k in range(4):
            s._sr(k)
        self.assertIsNone(s.fallback)
        self.assertEqual(len(s.calls), 4)

    def test_fallback_clip_fails_the_quality_gate(self):
        orig = cam_sr.gate_numbers
        cam_sr.gate_numbers = lambda e, o, fid_min=None: {"reasons": []}
        try:
            ok, reasons = cam_sr.quality_verdict({"enhanced": {}, "lanczos": {}, "sr_fallback": {"at_frame": 7, "sr_seconds": 905.0, "limit": 900}})
            self.assertFalse(ok)
            self.assertTrue(any("time budget" in r and "not shipped" in r for r in reasons))
            ok2, _ = cam_sr.quality_verdict({"enhanced": {}, "lanczos": {}})
            self.assertTrue(ok2)
        finally:
            cam_sr.gate_numbers = orig


class WiringTest(unittest.TestCase):
    def test_render_marks_every_stage_and_the_budgets_fit_the_step(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "clip_engine", "render_clip.py")).read()
        for name in ("cam_resolve", "cam_diag", "asr", "plan_and_track", "captions_check", "loudnorm", "cam_prep",
                     "encode_cam_sr", "final_checks"):
            self.assertIn(f'stagewatch.begin("{name}"', src)
        self.assertIn("stagewatch.close(", src)
        self.assertLess(cam_sr.SR_HARD_LIMIT_SEC, 1200)        # SR limit sits inside the encode stage budget

    def test_ffmpeg_calls_have_timeouts(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "clip_engine", "render_clip.py")).read()
        self.assertEqual(src.count("timeout=SUBPROCESS_TIMEOUT"), 3)
        self.assertIn("dec.wait(timeout=60)", src)
        self.assertIn("enc.wait(timeout=300)", src)


if __name__ == "__main__":
    unittest.main()
