"""Start picking searches the whole moment and never loosens the opening gate."""
import unittest

import numpy as np

from clip_engine import gates, hooks, trim

FPS = 10
H, W = 54, 30


def strip(n_s=48, moves_from=5.0, moves_until=None):
    """Shown strip: frozen noise until `moves_from`, then changing noise (until `moves_until`)."""
    rng = np.random.default_rng(3)
    base = rng.integers(40, 200, (H, W)).astype(np.uint8)
    out = []
    for i in range(int(n_s * FPS)):
        t = i / FPS
        live = t >= moves_from and (moves_until is None or t < moves_until)
        out.append(rng.integers(0, 255, (H, W)).astype(np.uint8) if live else base)
    return np.stack(out)


def words(spec):
    return [{"w": w, "s": s, "e": e} for w, s, e in spec]


LOUD = np.full(int(48 / 0.05), -20.0)


class PickStart(unittest.TestCase):
    def test_skips_the_frozen_head_and_lands_before_a_word(self):
        ws = words([("bro", 1.0, 1.4), ("give", 1.5, 1.8), ("me", 6.0, 6.2), ("that", 6.3, 6.6), ("now", 6.7, 7.0)])
        t, info = trim.pick_start(strip(), FPS, LOUD, 0.05, ws, 0.0, 48.0)
        self.assertIsNotNone(t)
        self.assertGreaterEqual(t, 4.65)                   # gate allows a 0.35 s lead-in freeze
        self.assertFalse(trim.inside_word(ws, t))
        self.assertGreaterEqual(48.0 - t, 25.0)
        self.assertEqual(info["reason"], "speech follows")

    def test_gate_is_the_real_gate_on_the_chosen_cut(self):
        ws = words([("a", 6.0, 6.3), ("b", 6.4, 6.7)])
        s = strip()
        t, _ = trim.pick_start(s, FPS, LOUD, 0.05, ws, 0.0, 48.0)
        i0 = int(round(t * FPS))
        self.assertTrue(gates.opening_gate(s[i0:i0 + 30], FPS)[0])

    def test_never_leaves_less_than_25_s(self):
        ws = words([("a", 30.0, 30.3), ("b", 30.4, 30.7)])
        t, info = trim.pick_start(strip(moves_from=29.0), FPS, LOUD, 0.05, ws, 0.0, 48.0)
        self.assertIsNone(t)                               # motion only from 29 s: 19 s left
        self.assertIsNone(info["start_s"])

    def test_skips_the_moment_when_the_shown_strip_never_moves(self):
        ws = words([("a", 6.0, 6.3), ("b", 6.4, 6.7)])
        t, info = trim.pick_start(strip(moves_from=99.0), FPS, LOUD, 0.05, ws, 0.0, 48.0)
        self.assertIsNone(t)
        self.assertGreater(info["shown_strip_frozen"], 0)

    def test_silence_is_never_a_start(self):
        t, info = trim.pick_start(strip(moves_from=0.0), FPS, np.full(960, -90.0), 0.05, [], 0.0, 48.0)
        self.assertIsNone(t)

    def test_loud_peak_is_the_fallback_without_speech(self):
        rms = np.full(960, -30.0)
        rms[int(8.0 / 0.05):int(8.3 / 0.05)] = -10.0
        t, info = trim.pick_start(strip(moves_from=0.0), FPS, rms, 0.05, [], 0.0, 48.0)
        self.assertIsNotNone(t)
        self.assertEqual(info["reason"], "loud peak follows (no speech)")
        self.assertGreaterEqual(t, 6.0 - 1e-6)             # peak must fall inside the 2 s after the start

    def test_a_word_sliver_does_not_count_as_speech(self):
        ws = words([("bro", 0.0, 0.03)])
        self.assertFalse(trim.speech_follows(ws, 0.0))

    def test_crop_path_follows_the_crop(self):
        g = np.zeros((3, 4, 100), np.uint8)
        g[0, :, 10:20] = 9
        g[2, :, 70:80] = 7
        s = trim.crop_cols_path(g, [0.10, 0.10, 0.70], 0.10)
        self.assertEqual(s.shape, (3, 4, 10))
        self.assertTrue((s[0] == 9).all() and (s[2] == 7).all())


class HookFromSpeech(unittest.TestCase):
    def test_sliver_is_not_a_hook(self):
        ws = words([("bro", 0.0, 0.03), ("later", 2.9, 3.4)])
        self.assertEqual(hooks.choose_hook("", ws)[0], "")

    def test_built_from_words_spoken_in_the_first_3_s(self):
        ws = words([("Bro", 0.1, 0.4), ("give", 0.5, 0.8), ("me", 0.9, 1.0), ("that.", 1.1, 1.5), ("Next", 2.0, 2.3)])
        text, src, ok = hooks.choose_hook("", ws)
        self.assertEqual((text, src, ok), ("Bro give me that", "speech_first3s", True))

    def test_a_pause_ends_the_line(self):
        ws = words([("wait", 0.1, 0.4), ("what", 0.5, 0.8), ("so", 2.0, 2.2), ("yeah", 2.3, 2.6)])
        self.assertEqual(hooks.choose_hook("", ws)[0], "wait what")


if __name__ == "__main__":
    unittest.main()
