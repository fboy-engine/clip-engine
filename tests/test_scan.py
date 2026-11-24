"""Scan helpers on synthetic frames: confident cam (stable overlay, no border), not-confident clip still gets a drawn best candidate,
window rule, one-line report. Stdlib unittest only."""
import os
import tempfile
import unittest

import numpy as np

try:
    import cv2
except ImportError:     # pragma: no cover
    raise unittest.SkipTest("opencv not installed")
from clip_engine import scan                    # noqa: E402
try:
    from test_stable_overlay import frames, face_at          # unittest discover -s tests
except ImportError:     # pragma: no cover
    from tests.test_stable_overlay import frames, face_at   # noqa: E402


def color(fr):
    return [cv2.cvtColor(g, cv2.COLOR_GRAY2BGR) for g in fr]


class Scan(unittest.TestCase):
    def test_window(self):
        self.assertEqual(scan.window_of(60), (2.0, 27.0))
        self.assertEqual(scan.window_of(19), (2.0, 17.0))

    def test_confident_cam_is_drawn_and_reported(self):
        res, ann, sheet = scan.scan_frames(color(frames(cam_level=145)), face_fn=face_at(840, 100, 60), name="c1")   # edge too weak for the border finder
        self.assertTrue(res["ok"] and res["method"] == "stable_overlay" and res["face_hit_pct"] >= 90)
        self.assertEqual(ann.shape, (540, 960, 3))
        self.assertEqual(sheet.shape[1], scan.SHEET_W)
        self.assertEqual(sheet.shape[0], 3 * 360)
        self.assertTrue((ann[:, :, 1] == 220).any())            # green rectangle pixels
        self.assertIn("CONFIDENT", scan.line(res))

    def test_not_confident_clip_still_gets_frames_and_reasons(self):
        res, ann, sheet = scan.scan_frames(color(frames(cam=None)), face_fn=face_at(480, 270, 60), name="c2")
        self.assertTrue(not res["ok"] and res["reasons"] and res["rect_px"])
        self.assertIn("not confident", scan.line(res))
        self.assertTrue(((ann[:, :, 2] == 255) & (ann[:, :, 0] == 0)).any())      # red rectangle pixels

    def test_save_writes_both_jpgs(self):
        res, ann, sheet = scan.scan_frames(color(frames(cam_level=145)), face_fn=face_at(840, 100, 60), name="c3")
        with tempfile.TemporaryDirectory() as d:
            scan.save(res, ann, sheet, d, "c3")
            self.assertTrue(os.path.exists(os.path.join(d, "scan_annotated", "c3.jpg")) and os.path.exists(os.path.join(d, "scan", "c3.jpg")))


if __name__ == "__main__":
    unittest.main()
