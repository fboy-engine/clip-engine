import unittest

import numpy as np

from clip_engine import trim


def W(text, s, e):
    return {"w": text, "s": s, "e": e}


def talk(a, b, step=0.6):
    out, t = [], a
    while t + 0.35 <= b:
        out.append(W("word", round(t, 2), round(t + 0.35, 2)))
        t += step
    return out


class Coverage(unittest.TestCase):
    def test_full_and_half(self):
        self.assertAlmostEqual(trim.window_coverage(talk(0, 10, 0.4), 0, 10), 1.0, delta=0.05)
        self.assertAlmostEqual(trim.window_coverage(talk(0, 5, 0.4), 0, 10), 0.51, delta=0.03)

    def test_words_outside_are_ignored(self):
        self.assertEqual(trim.window_coverage(talk(20, 30), 0, 10), 0.0)


class DenseCut(unittest.TestCase):
    def test_prefers_the_dense_stretch(self):
        words = [W("a", float(t), t + 0.4) for t in range(0, 14, 2)] + talk(14, 50)
        starts = [round(w["s"] - 0.08, 3) for w in words]
        t0, t1, info = trim.dense_cut(words, starts, 52.0)
        self.assertGreaterEqual(t0, 13.9)
        self.assertTrue(25.0 <= t1 - t0 <= 30.0)
        self.assertGreater(info["coverage_planned"], 0.85)

    def test_never_outside_length_rules(self):
        words = talk(0, 60)
        t0, t1, _ = trim.dense_cut(words, [0.0, 3.0, 9.0], 60.0)
        self.assertTrue(25.0 <= t1 - t0 <= trim.DENSE_MAX)

    def test_no_cut_point_raises(self):
        with self.assertRaises(ValueError):
            trim.dense_cut(talk(0, 20), [0.0], 20.0)

    def test_cut_is_after_a_word_not_inside(self):
        words = talk(0, 60)
        t0, t1, _ = trim.dense_cut(words, [0.0], 60.0)
        self.assertFalse(trim.inside_word(words, t1))


class ValidStarts(unittest.TestCase):
    def test_same_gates_as_pick_start(self):
        rng = np.random.default_rng(0)
        strip = rng.integers(0, 255, (400, 27, 48)).astype(np.uint8)
        rms = np.full(800, -20.0)
        words = talk(1, 40)
        starts, info = trim.valid_starts(strip, 10, rms, 0.05, words, 0.0, 40.0, min_left=25.0)
        self.assertTrue(starts)
        self.assertTrue(all(s <= 15.0 + 1e-6 for s in starts))
        self.assertFalse(any(trim.inside_word(words, s) for s in starts))

    def test_frozen_strip_has_no_start(self):
        strip = np.zeros((400, 27, 48), dtype=np.uint8)
        starts, _ = trim.valid_starts(strip, 10, np.full(800, -20.0), 0.05, talk(1, 40), 0.0, 40.0)
        self.assertEqual(starts, [])


if __name__ == "__main__":
    unittest.main()
