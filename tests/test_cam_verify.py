import unittest

from clip_engine import exclusion as ex, layout, vision


def obs(n, fw=70.0, cx=190.0, cy=520.0, jitter=3.0):
    return [(i * 0.5, cx + (i % 3) * jitter, cy + (i % 2) * jitter, fw) for i in range(n)]


class VerifyCamTests(unittest.TestCase):
    RECT = (392.0, 221.0)

    def test_large_stable_face_verifies(self):
        ok, info, large = vision.verify_cam(obs(20), 24, self.RECT)
        self.assertTrue(ok, info)
        self.assertEqual(len(large), 20)

    def test_small_faces_are_false_hits(self):
        ok, info, _ = vision.verify_cam(obs(24, fw=20.0), 24, self.RECT)
        self.assertFalse(ok)
        self.assertEqual(info["large_faces"], 0)

    def test_too_few_hits_fail(self):
        self.assertFalse(vision.verify_cam(obs(4), 24, self.RECT)[0])

    def test_wandering_faces_fail(self):
        wander = [(i * 0.5, 20.0 + 45.0 * i, 40.0 + 30.0 * (i % 5), 70.0) for i in range(20)]
        self.assertFalse(vision.verify_cam(wander, 20, self.RECT)[0])

    def test_no_samples_fail(self):
        self.assertFalse(vision.verify_cam([], 0, self.RECT)[0])


class SearchRegionTests(unittest.TestCase):
    def test_wide_detector_is_gone(self):
        self.assertFalse(hasattr(vision, "wide_face"))
        self.assertFalse(hasattr(vision, "_extra_cascades"))

    def test_left_right_and_mid_edge_are_searched(self):
        for name in ("top-left", "top-right", "bottom-left", "bottom-right", "mid-left", "mid-right",
                     "top-mid", "bottom-mid"):
            self.assertIn(name, vision.CORNER_SEARCH)

    def test_every_search_region_has_a_nominal_cam_box(self):
        for name in vision.CORNER_SEARCH:
            self.assertIn(name, layout.NOMINAL_CAM, name)

    def test_est_cam_rect_works_for_mid_edge(self):
        r = layout.est_cam_rect("mid-left", [(0.0, 190.0, 540.0, 70.0)], 1920, 1080)
        self.assertEqual(len(r), 4)


class SeedTests(unittest.TestCase):
    def test_cam_zone_seed_fractions(self):
        z = ex.zones_for("LHoHInbPkk0", 1920, 1080)
        self.assertEqual(ex.cam_seed_frac(z, 1920, 1080), (0.0, 0.4, 0.2042, 0.6046))

    def test_no_zone_no_seed(self):
        self.assertIsNone(ex.cam_seed_frac({}, 1920, 1080))


if __name__ == "__main__":
    unittest.main()
