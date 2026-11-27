import unittest

from clip_engine.cropplan import keep_in_frame


class KeepInFrame(unittest.TestCase):
    W, CW = 1920, 845

    def test_lagging_centre_is_pulled_so_the_box_fits(self):
        box = [0.50, 0, 0.84, 1]                      # 960..1613 px (653 px wide), centre 1286
        out = keep_in_frame([0.5], [box], self.CW, self.W)     # lagging crop centre at 960
        c = out[0] * self.W
        self.assertGreaterEqual(box[0] * self.W, c - self.CW / 2)
        self.assertLessEqual(box[2] * self.W, c + self.CW / 2)

    def test_already_inside_is_unchanged(self):
        box = [0.45, 0, 0.55, 1]
        self.assertEqual(keep_in_frame([0.5], [box], self.CW, self.W), [0.5])

    def test_wider_than_crop_and_missing_box_are_left_alone(self):
        wide = [0.10, 0, 0.60, 1]                     # 960 px > 845 px
        self.assertEqual(keep_in_frame([0.3, 0.4], [wide, None], self.CW, self.W), [0.3, 0.4])


if __name__ == "__main__":
    unittest.main()
