import unittest

import numpy as np

from clip_engine import cam_enhance, cam_sr

try:
    import cv2
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False


def apply(m, x, y):
    return (m[0][0] * x + m[0][2], m[1][1] * y + m[1][2])


class Geometry(unittest.TestCase):
    def test_roi_union_covers_every_box_with_margin_and_stays_in_region(self):
        boxes = [(100.2, 50.7, 360.0, 180.0), (104.9, 52.1, 360.0, 180.0)]
        x, y, w, h = cam_enhance.roi_union(boxes, (90, 40, 500, 260), margin=6)
        self.assertEqual((x, y), (94, 44))
        self.assertGreaterEqual(x + w, 105 + 360 + 6 - 1)
        self.assertLessEqual(x + w, 500)
        self.assertLessEqual(y + h, 260)
        x2, y2, w2, h2 = cam_enhance.roi_union(boxes, (100, 50, 470, 232), margin=6)
        self.assertEqual((x2, y2), (100, 50))
        self.assertLessEqual(x2 + w2, 470)

    def test_block_matrix_maps_box_edges_to_block_edges(self):
        for scale in (1, 4):
            left, top, w, h, ox, oy = 12.37, 8.81, 355.5, 177.75, 6.0, 3.0
            m = cam_enhance.block_matrix(left, top, w, h, ox, oy, scale=scale)
            # source pixel INDEX whose left edge is the box's left edge: index = edge - 0.5 (edges at index + 0.5)
            u0, v0 = apply(m, (left - ox) * scale - 0.5, (top - oy) * scale - 0.5)
            u1, v1 = apply(m, (left + w - ox) * scale - 0.5, (top + h - oy) * scale - 0.5)
            self.assertAlmostEqual(u0, -0.5, places=4)
            self.assertAlmostEqual(v0, -0.5, places=4)
            self.assertAlmostEqual(u1, 1080 - 0.5, places=3)
            self.assertAlmostEqual(v1, 540 - 0.5, places=3)

    def test_block_is_always_exactly_the_fixed_size_for_any_cam_size(self):
        for w in (240.0, 360.0, 517.3):
            m = cam_enhance.block_matrix(0, 0, w, w / 2, 0, 0, scale=1)
            u1, v1 = apply(m, w - 0.5, w / 2 - 0.5)
            self.assertAlmostEqual(u1, 1080 - 0.5, places=3)
            self.assertAlmostEqual(v1, 540 - 0.5, places=3)

    def test_window_4x_inside_picture_and_origin_in_source_px(self):
        roi = (100, 50, 372, 192)
        box = (106.0, 56.0, 360.0, 180.0)
        (x0, y0, x1, y1), org = cam_enhance.window_4x(box, roi, (192 * 4, 372 * 4))
        self.assertTrue(0 <= x0 < x1 <= 372 * 4 and 0 <= y0 < y1 <= 192 * 4)
        self.assertAlmostEqual(org[0], 100 + x0 / 4)
        self.assertGreaterEqual(x1 - x0, 360 * 4)

    def test_prepass_order_fps_then_deblock_then_crop_then_denoise(self):
        cmd = cam_enhance.prepass_cmd("s.mp4", 45.0, 40.0, 15, "deblock=filter=weak:block=8", (10, 20, 372, 192))
        vf = cmd[cmd.index("-vf") + 1]
        self.assertEqual(vf, f"fps=15,deblock=filter=weak:block=8,crop=372:192:10:20,{cam_sr.DENOISE_VF}")
        vf2 = cam_enhance.prepass_cmd("s.mp4", 0, 10, 20, "", (0, 0, 8, 8))[cam_enhance.prepass_cmd("s.mp4", 0, 10, 20, "", (0, 0, 8, 8)).index("-vf") + 1]
        self.assertTrue(vf2.startswith("fps=20,crop="))
        self.assertIn("-frames:v", cmd)


class Choices(unittest.TestCase):
    REAL = {"lanczos": {"sec_per_frame": 0.0039}, "fsrcnn": {"sec_per_frame": 0.0299},
            "edsr": {"sec_per_frame": 19.2419}, "realesrgan": {"sec_per_frame": 0.591}}

    def test_realesrgan_is_the_pick_and_20fps_fits_on_the_measured_numbers(self):
        sel = cam_sr.choose_b3(self.REAL)
        self.assertEqual(sel["method"], "realesrgan")
        self.assertEqual(sel["cam_fps"], 20)
        self.assertIn("edsr", sel["rejected"] if "edsr" in sel["rejected"] else {"edsr": ""})

    def test_slow_runner_lowers_the_fps_then_falls_to_next_best(self):
        slow = dict(self.REAL, realesrgan={"sec_per_frame": 1.0})
        self.assertEqual(cam_sr.choose_b3(slow)["cam_fps"], 12)
        hopeless = dict(self.REAL, realesrgan={"sec_per_frame": 9.0})
        sel = cam_sr.choose_b3(hopeless)
        self.assertEqual(sel["method"], "fsrcnn")
        self.assertIn("realesrgan", sel["rejected"])

    def test_unavailable_model_falls_back_and_lanczos_is_flagged(self):
        sel = cam_sr.choose_b3(dict(self.REAL, realesrgan={"error": "ModelError: download failed"}))
        self.assertEqual(sel["method"], "fsrcnn")
        only = {"lanczos": {"sec_per_frame": 0.004}, "realesrgan": {"error": "x"}}
        self.assertEqual(cam_sr.choose_b3(only)["method"], "lanczos")

    def test_plan_cam_fps_lowers_for_a_long_clip(self):
        self.assertEqual(cam_sr.plan_cam_fps(0.72, 45, 20), 15)
        self.assertEqual(cam_sr.plan_cam_fps(0.3, 40, 20), 20)
        self.assertIsNone(cam_sr.plan_cam_fps(9.0, 45, 20))
        self.assertEqual(cam_sr.plan_cam_fps(0.3, 40, 15), 15)      # never above the cap

    def test_gate_needs_all_three_metrics(self):
        base = {"blockiness": 1.0, "edge": 20.0, "fidelity": 1.0}
        good = {"fidelity": 0.97, "blockiness": 0.99, "edge": 25.0}
        self.assertTrue(cam_sr.gate_numbers(good, base)["ok"])
        for bad in ({"fidelity": 0.95}, {"blockiness": 1.0}, {"edge": 20.0}):
            r = cam_sr.gate_numbers(dict(good, **bad), base)
            self.assertFalse(r["ok"])
            self.assertEqual(len(r["reasons"]), 1)

    def test_choose_blend_takes_the_largest_share_that_passes(self):
        base = {"blockiness": 1.0, "edge": 20.0, "fidelity": 1.0}
        rows = {1.0: {"fidelity": 0.95, "blockiness": 1.1, "edge": 35.0},
                0.7: {"fidelity": 0.962, "blockiness": 0.99, "edge": 28.0},
                0.4: {"fidelity": 0.98, "blockiness": 0.98, "edge": 24.0}}
        self.assertEqual(cam_sr.choose_blend(rows, base)["blend"], 0.7)
        none = {1.0: {"fidelity": 0.9, "blockiness": 1.2, "edge": 19.0}}
        r = cam_sr.choose_blend(none, base)
        self.assertIsNone(r["blend"])
        self.assertTrue(r["reasons"])

    def test_ghost_verdict(self):
        ok, _ = cam_sr.ghost_verdict([0.97, 0.971], [0.965, 0.96, 0.968])
        self.assertTrue(ok)
        ok, d = cam_sr.ghost_verdict([0.97, 0.971], [0.90, 0.92, 0.91])
        self.assertFalse(ok)
        self.assertIn("in-between", d)
        self.assertFalse(cam_sr.ghost_verdict([], [0.9])[0])


@unittest.skipUnless(HAVE_CV2, "OpenCV not installed here: the pixel paths run in Actions")
class Pixels(unittest.TestCase):
    def test_to_block_is_exactly_1080x540_for_a_subpixel_box(self):
        img4 = np.random.default_rng(1).integers(0, 255, (192 * 4, 372 * 4, 3), dtype=np.uint8)
        out = cam_enhance.to_block(img4, (106.4, 56.7, 355.3, 177.65), (100, 50, 372, 192))
        self.assertEqual(out.shape, (540, 1080, 3))


if __name__ == "__main__":
    unittest.main()
