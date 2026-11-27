import unittest

from clip_engine.cropplan import dark_exempt


class DarkExempt(unittest.TestCase):
    def test_only_dark_undetected_not_inside_samples(self):
        src = ["person", "none", "motion", "motion", "none"]
        box = [[0.4, 0, 0.5, 1], None, [0.1, 0, 0.2, 1], [0.1, 0, 0.2, 1], None]
        times = [0.0, 0.5, 1.0, 1.5, 2.0]
        luma = [20, 20, 20, 90, 90]                 # samples 3 and 4 are bright
        out = dark_exempt(src, box, times, [1.0, 1.5], luma, 50.0)
        self.assertEqual(out, [1, 2])               # person never exempt; bright frames never exempt

    def test_inside_motion_sample_is_not_exempt(self):
        out = dark_exempt(["motion"], [[0.4, 0, 0.5, 1]], [0.0], [], [10], 50.0)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
