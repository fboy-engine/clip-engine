import unittest

import numpy as np

from clip_engine import layout, subject, vision


class Layout(unittest.TestCase):
    def test_blocks_add_up_and_panel_is_bigger(self):
        self.assertEqual(layout.CAM_H + layout.PANEL_H, 1920)
        self.assertGreater(layout.PANEL_H, layout.CAM_H)
        self.assertAlmostEqual(layout.CAM_H / 1920, 0.28, delta=0.005)

    def test_panel_crop_is_845_by_1080_on_1080p(self):
        w, h = layout.panel_wh(1920, 1080)
        self.assertAlmostEqual(w, 845.2, delta=0.5)
        self.assertEqual(h, 1080)

    def test_cam_boxes_are_2_to_1_inside_region_and_moderate_upscale(self):
        region = (0, 648, 700, 1080)
        path = (np.full(60, 250.0), np.full(60, 850.0), 120.0)
        bx = layout.cam_boxes(path, region)
        for left, top, w, h in bx:
            self.assertAlmostEqual(w / h, 2.0, places=6)
            self.assertGreaterEqual(left, 0)
            self.assertLessEqual(left + w, 700 + 1e-6)
            self.assertGreaterEqual(top, 648)
            self.assertLessEqual(top + h, 1080 + 1e-6)
        self.assertLessEqual(layout.upscale(bx), 3.0 + 1e-6)

    def test_small_face_does_not_over_zoom(self):
        bx = layout.cam_boxes((np.full(5, 300.0), np.full(5, 900.0), 40.0), (0, 600, 800, 1080))
        self.assertGreaterEqual(bx[0][2], 1080 / 3.0 - 1e-6)

    def test_face_path_is_smooth_and_width_constant(self):
        obs = [(i * 0.5, 250 + (30 if i % 2 else -30), 850.0, 110.0) for i in range(20)]
        cx, cy, fw = layout.face_path(obs, 600, 60)
        self.assertEqual(len(cx), 600)
        self.assertEqual(fw, 110.0)
        self.assertLess(np.abs(np.diff(cx)).max(), 2.0)

    def test_clean_obs_drops_far_outlier(self):
        obs = [(i, 250.0, 850.0, 110.0) for i in range(8)] + [(9, 1500.0, 300.0, 90.0)]
        self.assertEqual(len(layout.clean_obs(obs)), 8)

    def test_est_rect_contains_nominal_and_face(self):
        r = layout.est_cam_rect("bottom-left", [(0, 400, 900, 100.0)], 1920, 1080)
        self.assertEqual(r[0], 0.0)
        self.assertGreaterEqual(r[2], 0.26)
        self.assertGreaterEqual(r[2] * 1920, 400 + 100)


class CamChoice(unittest.TestCase):
    def test_stable_face_in_one_corner_wins(self):
        faces = {"bottom-left": [(i * 0.5, 250 + i % 2, 850, 110.0) for i in range(30)],
                 "top-right": [(i * 3.0, 1500 + 200 * (i % 3), 200 + 100 * i, 90.0) for i in range(8)],
                 "top-left": [], "bottom-right": []}
        cam, stats = vision.choose_cam(faces, 40, 1920, 1080)
        self.assertEqual(cam["corner"], "bottom-left")
        self.assertGreaterEqual(cam["hit_pct"], 70)

    def test_no_face_means_no_cam(self):
        cam, stats = vision.choose_cam({"bottom-left": [(0, 1, 1, 1.0)]}, 40, 1920, 1080)
        self.assertIsNone(cam)


class Subject(unittest.TestCase):
    def B(self, cx, w=0.08, s=1.0):
        return (cx - w / 2, 0.3, cx + w / 2, 0.9, s)

    def test_holds_through_misses_and_flags_untracked(self):
        dets = [[self.B(0.5)], [self.B(0.52)], [], [], [self.B(0.55)]]
        r = subject.build(dets)
        self.assertEqual(r["tracked"], [True, True, False, False, True])
        self.assertAlmostEqual(r["cx"][2], 0.52)

    def test_leading_miss_is_backfilled(self):
        r = subject.build([[], [], [self.B(0.4)]])
        self.assertAlmostEqual(r["cx"][0], 0.4)

    def test_prefers_nearest_to_previous(self):
        r = subject.build([[self.B(0.3)], [self.B(0.32, 0.05), self.B(0.8, 0.05)]])
        self.assertAlmostEqual(r["cx"][1], 0.32)

    def test_inside_stats_lists_cut_times(self):
        boxes = [(0.30, 0.3, 0.38, 0.9, 1), (0.05, 0.3, 0.13, 0.9, 1), None]
        s = subject.inside_stats(boxes, [0.0, 0.5, 1.0], [400.0] * 3, 845.0, 1920)
        self.assertEqual(s["cut_times"], [0.5])
        self.assertEqual(s["inside_pct"], 50.0)
        self.assertAlmostEqual(s["tracked_pct"], 66.7, places=1)

    def test_output_classification(self):
        d = [[(100, 50, 200, 400, 1)], [(0, 50, 90, 400, 1)], [], [(1000, 50, 1080, 400, 1)]]
        lab = subject.classify_output(d, 1080)
        self.assertEqual(lab, ["ok", "cut", "unseen", "cut"])
        s = subject.summarize_output(lab, [0, 0.5, 1.0, 1.5])
        self.assertEqual(s["cut_times"], [0.5, 1.5])
        self.assertEqual(s["inside_pct"], 33.3)


if __name__ == "__main__":
    unittest.main()
