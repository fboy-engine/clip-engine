import unittest

from clip_engine import exclusion as ex, layout, render_clip


class VodIdTests(unittest.TestCase):
    def test_urls(self):
        for u in ("https://www.youtube.com/watch?v=LHoHInbPkk0", "https://youtu.be/LHoHInbPkk0?t=5",
                  "https://www.youtube.com/live/LHoHInbPkk0", "LHoHInbPkk0"):
            self.assertEqual(ex.vod_id_from_url(u), "LHoHInbPkk0")

    def test_garbage_is_empty(self):
        self.assertEqual(ex.vod_id_from_url("nope"), "")
        self.assertEqual(ex.vod_id_from_url(""), "")


class PanelBoundsTests(unittest.TestCase):
    def test_zones_set_safe_range(self):
        z = ex.zones_for("LHoHInbPkk0")
        self.assertEqual(render_clip.panel_bounds(None, z, 1920, 845), (397, 885))

    def test_infeasible_crop_raises(self):
        with self.assertRaises(ValueError):
            render_clip.panel_bounds(None, ex.zones_for("LHoHInbPkk0"), 1920, 1400)

    def test_no_zones_uses_cam_rule(self):
        lo, hi = render_clip.panel_bounds((0.0, 0.6, 0.2, 1.0), {}, 1920, 600)
        self.assertGreater(lo, 0.2 * 1920 - 1)

    def test_boxes_from_bounds_pass_the_gate(self):
        z = ex.zones_for("LHoHInbPkk0")
        b = render_clip.panel_bounds(None, z, 1920, 845)
        xs = [b[0], 600, b[1]]
        bxs = render_clip.boxes(xs, [0.0, 0.04, 0.05], 1920, 1080, b[0], layout.PANEL_ASPECT)
        self.assertTrue(ex.overlay_gate(bxs, z)[0])

    def test_left_cam_crop_fails_the_gate(self):
        z = ex.zones_for("LHoHInbPkk0")
        bxs = render_clip.boxes([100.0], [0.0], 1920, 1080, 0.0, layout.PANEL_ASPECT)
        self.assertFalse(ex.overlay_gate(bxs, z)[0])


class LayoutReportTests(unittest.TestCase):
    def test_report_fields(self):
        bxs = [(400.0, 0.0, 845.0, 1080.0), (500.0, 0.0, 845.0, 1080.0)]
        rep = layout.layout_report(1920, 1080, (0.0, 0.4, 0.204, 0.6), {"box_px": [540, 270], "upscale": 2.0}, bxs,
                                   {"checked": True, "ok": True})
        self.assertEqual(rep["top_block"], {"w": 1080, "h": 540, "pct_of_height": 28.1})
        self.assertEqual(rep["gameplay_panel"], {"y0": 540, "w": 1080, "h": 1380})
        self.assertEqual(rep["crop_rect_px"]["left_min"], 400.0)
        self.assertEqual(rep["crop_rect_px"]["left_max"], 500.0)
        self.assertEqual(rep["panel_scale"], 1.278)
        self.assertEqual(rep["cam_upscale"], 2.0)
        self.assertTrue(rep["exclusion"]["ok"])

    def test_report_without_cam(self):
        rep = layout.layout_report(1920, 1080, None, None, [])
        self.assertIsNone(rep["cam_rect_px"])
        self.assertEqual(rep["exclusion"], {"checked": False})


if __name__ == "__main__":
    unittest.main()
