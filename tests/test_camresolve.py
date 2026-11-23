"""A3-2: cam/zone resolution before the render (stdlib unittest; the detector and the frame grab are replaced by fakes)."""
import json
import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from clip_engine import camfind, camresolve, layout, render_clip

LAUREN = {"width": 1920, "height": 1080, "frames": 24,
          "cam": {"rect_px": [1304, 0, 1920, 424], "rect_frac": [0.6792, 0.0, 1.0, 0.3926], "confidence": 0.989,
                  "sides": {"left": "line", "right": "edge", "top": "edge", "bottom": "line"}, "area_pct": 12.6,
                  "face": {"cx": 1470.0, "cy": 148.0, "w": 84.0, "n": 23, "hit": 0.958, "spread": 0.02}, "faces_in_rect": []},
          "chat": {"side": "right", "rect": (1728.0, 424.0, 1920.0, 1074.0), "pitch_px": 52},
          "zones": {"cam": (1304.0, 0.0, 1920.0, 424.0), "chat": (1728.0, 424.0, 1920.0, 1074.0),
                    "side_panel": (1304.0, 0.0, 1920.0, 1080.0)},
          "confidence": 0.989, "warnings": [], "evidence": {"vlines": [], "hlines": [], "faces": []}}


def _load(d):
    with open(os.path.join(d, "cam_find.json")) as f:
        return json.load(f)


class Pieces(unittest.TestCase):
    def test_is_auto_only_when_nothing_is_given(self):
        self.assertTrue(camresolve.is_auto({}, None, None))
        self.assertTrue(camresolve.is_auto({}, None, "auto"))
        self.assertFalse(camresolve.is_auto({"cam": (0, 0, 1, 1)}, None, ""))
        self.assertFalse(camresolve.is_auto({}, [0.7, 0, 1, 0.4], ""))
        self.assertFalse(camresolve.is_auto({}, None, "top-right"))
        self.assertFalse(camresolve.is_auto({}, None, "none"))

    def test_inset_only_on_border_lines(self):
        self.assertEqual(camresolve.inset_region((1304, 0, 1920, 424), LAUREN["cam"]["sides"]), (1307, 0, 1920, 421))
        self.assertEqual(camresolve.inset_region((10, 10, 100, 50), {"left": "edge", "top": "edge"}), (10, 10, 100, 50))

    def test_face_cam_is_one_constant_track_with_the_seeded_cam_keys(self):
        cam = camresolve.face_cam(LAUREN, 30.0)
        self.assertEqual(cam["corner"], "top-right")
        self.assertEqual([o[0] for o in cam["obs"]], [0.0, 15.0, 30.0])
        self.assertTrue(all(o[1:] == (1470.0, 148.0, 84.0) for o in cam["obs"]))
        self.assertTrue({"corner", "obs", "hit_pct", "rect", "how", "verify"} <= set(cam))
        path = layout.face_path(cam["obs"], 100, 60.0)
        self.assertAlmostEqual(path[0][50], 1470.0, 3)

    def test_full_window_of_the_real_cam_is_the_whole_cam_at_2_to_1(self):
        region = camresolve.inset_region((1304, 0, 1920, 424), LAUREN["cam"]["sides"])
        path = layout.face_path(camresolve.face_cam(LAUREN, 30.0)["obs"], 10, 60.0)
        box = layout.cam_boxes(path, region, full=True)[0]
        self.assertAlmostEqual(box[2], 613.0)
        self.assertAlmostEqual(box[3], 306.5)
        self.assertTrue(box[0] >= region[0] and box[0] + box[2] <= region[2] and box[1] >= region[1] and box[1] + box[3] <= region[3])
        self.assertAlmostEqual(layout.upscale([box]), 1080 / 613.0, 4)       # upscale through the existing cam chain
        old = layout.cam_boxes(path, region)[0]                              # the legacy rule is unchanged
        self.assertAlmostEqual(old[2], max(84 * layout.FACE_BOX_FACTOR, 1080 / 3.0))

    def test_big_cam_downscales_only_when_it_is_already_wide(self):
        path = (np.array([800.0]), np.array([400.0]), 200.0)
        box = layout.cam_boxes(path, (100, 100, 1500, 900), full=True)[0]
        self.assertEqual((box[2], box[3]), (1400.0, 700.0))
        self.assertTrue(layout.upscale([box]) < 1)


class Resolve(unittest.TestCase):
    def test_generic_success_writes_the_json_and_returns_the_cam(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(camfind, "detect_media", return_value=LAUREN) as dm:
            r = camresolve.resolve("m.mp4", 0.0, 36.0, 1920, 1080, d, {}, None, "", log=lambda *a: None)
            self.assertTrue(dm.called)
            self.assertTrue(r["generic"])
            self.assertEqual(r["region"], (1307, 0, 1920, 421))
            self.assertEqual(r["rect_px"], (1304.0, 0.0, 1920.0, 424.0))
            self.assertEqual(sorted(r["zones"]), ["cam", "chat", "side_panel"])
            self.assertEqual(r["cam_rect"], [0.6792, 0.0, 1.0, 0.3926])
            j = _load(d)
            self.assertTrue(j["ok"] and j["result"]["cam"]["rect_px"] == [1304, 0, 1920, 424])

    def test_not_confident_raises_with_reasons_and_leaves_the_evidence(self):
        err = camfind.CamNotConfident("no stable face in the sampled frames: pass cam_rect", {"faces": []})
        with tempfile.TemporaryDirectory() as d, mock.patch.object(camfind, "detect_media", side_effect=err), \
                mock.patch.object(camresolve, "draw_given", return_value=None):
            with self.assertRaises(camfind.CamNotConfident) as cm:
                camresolve.resolve("m.mp4", 0.0, 36.0, 1920, 1080, d, {}, None, None, log=lambda *a: None)
            self.assertIn("no stable face", str(cm.exception))
            j = _load(d)
            self.assertFalse(j["ok"])
            self.assertIn("no stable face", j["error"])

    def test_given_cam_rect_overrides_the_detector(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(camfind, "detect_media") as dm, \
                mock.patch.object(camresolve, "draw_given", return_value=None) as dg:
            r = camresolve.resolve("m.mp4", 0.0, 36.0, 1920, 1080, d, {}, [0.7, 0.0, 1.0, 0.4], "", log=lambda *a: None)
            self.assertFalse(dm.called)
            self.assertTrue(dg.called)                      # cam_find.jpg is still drawn
            self.assertFalse(r["generic"])
            self.assertEqual(r["cam_rect"], [0.7, 0.0, 1.0, 0.4])
            j = _load(d)
            self.assertEqual(j["source"], "given cam_rect")
            self.assertEqual(j["zones"]["cam"], [1344.0, 0.0, 1920.0, 432.0])

    def test_known_zones_and_gameplay_only_do_not_run_the_detector(self):
        for zones, corner, why in (({"cam": (0.0, 0.0, 10.0, 10.0)}, "", "known VOD zones"), ({}, "none", "gameplay only")):
            with tempfile.TemporaryDirectory() as d, mock.patch.object(camfind, "detect_media") as dm, \
                    mock.patch.object(camresolve, "draw_given", return_value=None):
                r = camresolve.resolve("m.mp4", 0.0, 36.0, 1920, 1080, d, zones, None, corner, log=lambda *a: None)
                self.assertFalse(dm.called or r["generic"])
                self.assertTrue(_load(d)["source"].startswith(why))


class Summary(unittest.TestCase):
    def test_run_summary_shows_the_cam_find_and_the_rendered_gate(self):
        r = {"cam_find": {"generic": True, "rect_px": [1304, 0, 1920, 424], "region_px": [1307, 0, 1920, 421], "confidence": 0.989,
                          "zones": {"cam": [1304, 0, 1920, 424], "chat": [1728, 424, 1920, 1074]}},
             "rendered_gate": {"ok": False, "reasons": ["frame 2: panel overlaps zone cam"], "frames": [{}, {}]}}
        s = render_clip.summary_md(r)
        self.assertIn("generic detector", s)
        self.assertIn("rendered gate: **FAIL** on 2 frames - frame 2: panel overlaps zone cam", s)
        self.assertEqual(render_clip.summary_md({}), "")


if __name__ == "__main__":
    unittest.main()
