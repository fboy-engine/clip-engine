"""The opening gate must be judged on the strip the viewer sees, not on the whole 16:9 frame."""
import unittest

import numpy as np

from clip_engine import asr_fix, gates, trim

FPS = 20
STRIP = (60 / 192, 60 / 192)       # left, width as fractions of the frame


def stack(n=240, h=108, w=192, move_inside_from=2.6):
    """The right side of the frame (outside the crop) moves from t=0; the shown strip (columns
    60..120) is frozen until `move_inside_from`."""
    rng = np.random.default_rng(1)
    base = rng.integers(60, 200, (h, w)).astype(np.float32)
    out = np.empty((n, h, w), np.uint8)
    for i in range(n):
        f = base.copy()
        f[:, 130:] = rng.integers(0, 255, (h, w - 130))
        if i / FPS >= move_inside_from:
            f[:, 60:120] = rng.integers(0, 255, (h, 60))
        out[i] = f.astype(np.uint8)
    return out


class ShownStrip(unittest.TestCase):
    def test_crop_cols_takes_the_shown_columns(self):
        g = stack()
        s = trim.crop_cols(g, *STRIP)
        self.assertEqual(s.shape, (240, 108, 60))
        self.assertTrue(np.array_equal(s[0], g[0][:, 60:120]))

    def test_crop_cols_clamps_to_the_frame(self):
        g = stack()
        self.assertEqual(trim.crop_cols(g, 0.95, 0.316).shape[2], 61)
        self.assertEqual(trim.crop_cols(g, -0.2, 0.316).shape[2], 61)

    def test_whole_frame_probe_starts_where_the_shown_picture_is_still_frozen(self):
        g = stack()
        whole = trim.choose_start(trim.motion_diffs(g), FPS, np.full(400, -20.0), 0.05)[0]
        self.assertIsNotNone(whole)
        self.assertLess(whole, 0.5)                          # old probe: "motion right away"
        ok, _, _ = gates.opening_gate(trim.crop_cols(g, *STRIP)[:60:2], 10)
        self.assertFalse(ok)                                 # the grade sees a frozen opening

    def test_visible_start_finds_where_the_shown_strip_really_moves(self):
        strip = trim.crop_cols(stack(), *STRIP)
        t, _ = trim.visible_start(strip, FPS, np.full(600, -20.0), 0.05)
        self.assertIsNotNone(t)
        self.assertGreaterEqual(t, 2.2)                      # gate allows <= 0.35 s of lead-in freeze
        self.assertLessEqual(t, 3.0)
        i0 = int(round(t * FPS))
        ok, _, _ = gates.opening_gate(strip[i0:i0 + 60:2], 10)   # the real gate agrees on that cut
        self.assertTrue(ok)

    def test_visible_start_zero_when_the_strip_moves_from_the_first_frame(self):
        strip = trim.crop_cols(stack(move_inside_from=0.0), *STRIP)
        self.assertEqual(trim.visible_start(strip, FPS, np.full(600, -20.0), 0.05)[0], 0.0)

    def test_visible_start_none_when_it_never_moves_or_is_silent(self):
        strip = trim.crop_cols(stack(move_inside_from=99.0), *STRIP)
        self.assertIsNone(trim.visible_start(strip, FPS, np.full(600, -20.0), 0.05)[0])
        strip2 = trim.crop_cols(stack(move_inside_from=0.0), *STRIP)
        self.assertIsNone(trim.visible_start(strip2, FPS, np.full(600, -90.0), 0.05)[0])


class SlicedWord(unittest.TestCase):
    @staticmethod
    def W(w, s, e):
        return {"w": w, "s": s, "e": e, "p": 0.9}

    def test_word_cut_by_the_start_is_dropped_when_mostly_outside(self):
        out = asr_fix.to_clip_time([self.W("bro", 44.9, 45.08), self.W("Give", 47.84, 48.2)], 45.05)
        self.assertEqual([w["w"] for w in out], ["Give"])

    def test_word_mostly_inside_is_kept(self):
        out = asr_fix.to_clip_time([self.W("really", 44.95, 45.55)], 45.05)
        self.assertEqual([w["w"] for w in out], ["really"])
        self.assertEqual(out[0]["s"], 0.0)


if __name__ == "__main__":
    unittest.main()
