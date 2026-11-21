import unittest

from clip_engine import exclusion as ex

VOD = "LHoHInbPkk0"


class ZoneTests(unittest.TestCase):
    def test_vod_zones_exact_on_1080p(self):
        z = ex.zones_for(VOD, 1920, 1080)
        self.assertEqual(z["cam"], (0, 432, 392, 653))
        self.assertEqual(z["chat"], (0, 653, 392, 800))
        self.assertEqual(z["counters"], (1735, 530, 1920, 600))
        self.assertEqual(z["timer"], (1750, 130, 1900, 180))

    def test_unknown_vod_has_no_zones(self):
        self.assertEqual(ex.zones_for("nope"), {})

    def test_zones_scale_with_source(self):
        z = ex.zones_for(VOD, 960, 540)
        self.assertEqual(z["cam"], (0, 216, 196, 326.5))

    def test_safe_range_matches_brief(self):
        lo, hi = ex.safe_x_range(ex.zones_for(VOD), 1920)
        self.assertEqual((lo, hi), (397, 1730))

    def test_rows_outside_a_zone_do_not_count(self):
        z = ex.zones_for(VOD)
        lo, hi = ex.safe_x_range(z, 1920, crop_top=0, crop_bottom=100)
        self.assertEqual((lo, hi), (0.0, 1920.0))


class BoundsTests(unittest.TestCase):
    def test_panel_845_fits(self):
        b = ex.crop_left_bounds(ex.zones_for(VOD), 1920, 845)
        self.assertEqual(b, (397, 885))

    def test_too_wide_crop_is_infeasible(self):
        self.assertIsNone(ex.crop_left_bounds(ex.zones_for(VOD), 1920, 1400))

    def test_clamp(self):
        b = (397, 885)
        self.assertEqual(ex.clamp_x(0, b), 397)
        self.assertEqual(ex.clamp_x(1500, b), 885)
        self.assertEqual(ex.clamp_x(600, b), 600)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.z = ex.zones_for(VOD)

    def test_clean_boxes_pass(self):
        boxes = [(400 + i, 0, 845, 1080) for i in range(0, 480, 20)]
        ok, detail, info = ex.overlay_gate(boxes, self.z)
        self.assertTrue(ok, detail)
        self.assertEqual(info["frames_checked"], len(boxes))

    def test_cam_in_panel_fails(self):
        ok, detail, info = ex.overlay_gate([(400, 0, 845, 1080), (300, 0, 845, 1080)], self.z)
        self.assertFalse(ok)
        self.assertEqual(info["hits"]["cam"], 1)
        self.assertEqual(info["hits"]["chat"], 1)
        self.assertIn("cam", detail)

    def test_counter_and_timer_fail(self):
        ok, _, info = ex.overlay_gate([(1000, 0, 845, 1080)], self.z)
        self.assertFalse(ok)
        self.assertEqual(info["hits"]["counters"], 1)
        self.assertEqual(info["hits"]["timer"], 1)

    def test_edge_touch_is_not_a_hit(self):
        self.assertEqual(ex.zone_hits((392, 0, 845, 1080), self.z), [])

    def test_no_frames_fails(self):
        self.assertFalse(ex.overlay_gate([], self.z)[0])


class CutTests(unittest.TestCase):
    def test_subject_inside(self):
        self.assertTrue(ex.subject_inside(500, 400, 845))
        self.assertFalse(ex.subject_inside(300, 400, 845))

    def test_clamped_crop_cuts_subject_near_cam(self):
        b = (397, 885)
        xs = [900] * 30 + [150] * 30 + [900] * 30
        cuts = ex.cut_stretches(xs, 30, 845, b)
        self.assertEqual(cuts, [(1.0, 2.0)])

    def test_no_detection_never_cuts(self):
        self.assertEqual(ex.cut_stretches([None] * 10, 30, 845, (397, 885)), [])

    def test_min_len_drops_blips(self):
        xs = [900] * 10 + [150] * 3 + [900] * 10
        self.assertEqual(ex.cut_stretches(xs, 30, 845, (397, 885), min_len=0.3), [])

    def test_open_ended_stretch_closes_at_end(self):
        xs = [900] * 10 + [150] * 10
        self.assertEqual(ex.cut_stretches(xs, 10, 845, (397, 885)), [(1.0, 2.0)])


if __name__ == "__main__":
    unittest.main()
