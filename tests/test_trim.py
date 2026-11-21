import unittest

import numpy as np

from clip_engine import balance, gates, grade_checks, hooks, trim


def gray_clip(n, freeze_until=0, still_until=0, seed=0, h=27, w=48):
    """n frames. Frozen (identical) up to freeze_until, barely moving to still_until, then noisy motion."""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (h, w)).astype(np.float32)
    out, cur = [], base.copy()
    for i in range(n):
        if i < freeze_until:
            pass
        elif i < still_until:
            cur = np.clip(cur + rng.normal(0, 0.3, cur.shape), 0, 255)
        else:
            cur = np.clip(cur + rng.normal(0, 14, cur.shape), 0, 255)
        out.append(cur.copy())
    return np.array(out, dtype=np.uint8)


def W(text, s, e):
    return {"w": text, "s": s, "e": e}


class FirstMotion(unittest.TestCase):
    def test_skips_a_frozen_opening(self):
        d = trim.motion_diffs(gray_clip(160, freeze_until=40))     # 2 s frozen at 20 fps
        i = trim.first_motion(d, 20)
        self.assertIsNotNone(i)
        self.assertAlmostEqual(i / 20, 2.0, delta=0.15)

    def test_skips_a_barely_moving_opening(self):
        d = trim.motion_diffs(gray_clip(200, freeze_until=10, still_until=80))
        self.assertGreaterEqual(trim.first_motion(d, 20) / 20, 3.5)

    def test_immediate_motion_starts_at_once(self):
        d = trim.motion_diffs(gray_clip(120))
        self.assertLessEqual(trim.first_motion(d, 20), 2)

    def test_all_frozen_returns_none(self):
        self.assertIsNone(trim.first_motion(trim.motion_diffs(gray_clip(120, freeze_until=120)), 20))

    def test_facecam_motion_is_masked_out(self):
        g = gray_clip(120, freeze_until=120).astype(np.float32)
        rng = np.random.default_rng(1)
        g[:, 15:, :20] = rng.integers(0, 255, g[:, 15:, :20].shape)      # noisy bottom-left cam
        g = g.astype(np.uint8)
        self.assertIsNotNone(trim.first_motion(trim.motion_diffs(g), 20))                 # fooled
        self.assertIsNone(trim.first_motion(trim.motion_diffs(g, (0.0, 0.5, 0.45, 1.0)), 20))  # masked

    def test_choose_start_waits_for_sound(self):
        d = trim.motion_diffs(gray_clip(160))                       # motion from the start
        rms = np.concatenate([np.full(20, -80.0), np.full(200, -20.0)])   # 1 s of silence at 50 ms hop
        off, info = trim.choose_start(d, 20, rms, 0.05)
        self.assertIsNotNone(off)
        self.assertGreaterEqual(off, 0.95)
        self.assertTrue(info["audio_ok"])

    def test_choose_start_none_when_always_silent(self):
        d = trim.motion_diffs(gray_clip(160))
        off, _ = trim.choose_start(d, 20, np.full(400, -80.0), 0.05)
        self.assertIsNone(off)

    def test_snap_start_forward_only(self):
        words = [W("hello", 9.0, 10.5), W("there", 10.7, 11.0)]
        t, note = trim.snap_start_forward(words, 10.0)
        self.assertGreaterEqual(t, 10.0)
        self.assertLess(t, 10.7)
        self.assertTrue(note)
        t2, _ = trim.snap_start_forward(words, 8.0)
        self.assertEqual(t2, 8.0)


class Payoff(unittest.TestCase):
    def setUp(self):
        self.words = [W("walking", 5.0, 5.4), W("slowly.", 5.5, 6.0), W("so", 14.0, 14.2), W("yeah.", 14.3, 14.6),
                      W("Everyone", 30.0, 30.4), W("dies", 30.45, 30.8), W("today!", 30.9, 31.4),
                      W("Juicy,", 32.0, 32.4), W("drive!", 32.45, 33.0), W("okay", 41.0, 41.3), W("then.", 41.4, 41.8)]

    def test_phrase_ends_after_the_named_beat(self):
        end, why, _ = trim.payoff_end(self.words, 0.0, 25.0, 45.0, phrase="Everyone dies today Juicy drive")
        self.assertGreater(end, 33.0)
        self.assertLess(end, 33.5)
        self.assertIn("phrase", why)

    def test_phrase_survives_asr_typos(self):
        self.assertIsNotNone(trim.match_phrase(self.words, "everyone dies today juicy drive"))
        words = [dict(w) for w in self.words]
        words[5]["w"] = "dyes"
        self.assertIsNotNone(trim.match_phrase(words, "everyone dies today juicy drive"))

    def test_heuristic_prefers_the_exclamation_with_a_lull(self):
        rms = np.full(1000, -35.0)                       # 50 s at 50 ms
        rms[int(30 / 0.05):int(33 / 0.05)] = -10.0       # loud beat
        end, why, _ = trim.payoff_end(self.words, 0.0, 25.0, 45.0, rms, 0.05)
        self.assertGreater(end, 33.0)
        self.assertLess(end, 34.0)
        self.assertIn("exclamation", why)

    def test_never_cuts_through_a_word(self):
        end, _, _ = trim.payoff_end(self.words, 0.0, 25.0, 45.0)
        for w in self.words:
            self.assertFalse(w["s"] < end < w["e"])

    def test_missing_phrase_falls_back_with_a_note(self):
        end, why, notes = trim.payoff_end(self.words, 0.0, 25.0, 45.0, phrase="completely different words")
        self.assertTrue(any("not found" in n for n in notes))
        self.assertGreater(end, 25.0)

    def test_no_cut_point_is_an_error(self):
        with self.assertRaises(ValueError):
            trim.payoff_end(self.words, 0.0, 50.0, 60.0)


class OpeningGate(unittest.TestCase):
    def test_frozen_start_fails(self):
        ok, detail, info = gates.opening_gate(gray_clip(30, freeze_until=10), 10)
        self.assertFalse(ok)
        self.assertGreaterEqual(info["longest_frozen_s"], 0.9)

    def test_barely_moving_fails(self):
        ok, _, info = gates.opening_gate(gray_clip(30, freeze_until=2, still_until=30), 10)
        self.assertFalse(ok)

    def test_moving_passes(self):
        ok, detail, info = gates.opening_gate(gray_clip(30), 10)
        self.assertTrue(ok, detail)
        self.assertLessEqual(info["first_motion_s"], 0.2)


class HookMatch(unittest.TestCase):
    def test_given_hook_spoken_in_first_3s_is_kept(self):
        words = [W("Everyone", 0.4, 0.8), W("dies", 0.9, 1.2), W("today!", 1.3, 1.8)]
        self.assertEqual(hooks.choose_hook("Everyone dies today!", words), ("Everyone dies today!", "given", True))

    def test_hook_only_said_late_is_replaced_by_the_opening_line(self):
        words = [W("Get", 0.3, 0.5), W("in", 0.6, 0.7), W("the", 0.8, 0.9), W("car!", 1.0, 1.4),
                 W("Everyone", 28.0, 28.4), W("dies", 28.5, 28.8), W("today!", 28.9, 29.3)]
        text, src, ok = hooks.choose_hook("Everyone dies today!", words)
        self.assertEqual(src, "speech_first3s")
        self.assertTrue(ok)
        self.assertIn("car", text)

    def test_no_speech_keeps_given_but_unverified(self):
        text, src, ok = hooks.choose_hook("Everyone dies today!", [W("late", 20.0, 20.4)])
        self.assertEqual((src, ok), ("given_unverified", False))

    def test_nothing_at_all(self):
        self.assertEqual(hooks.choose_hook("", []), ("", "none", False))


class ColdOpen(unittest.TestCase):
    def test_silent_ramp_in_is_late(self):
        sr = 16000
        pcm = np.concatenate([np.zeros(sr), 0.2 * np.random.default_rng(0).standard_normal(sr * 4)])
        self.assertGreaterEqual(balance.cold_open(pcm, sr)["onset_s"], 0.9)

    def test_sound_from_frame_one(self):
        sr = 16000
        pcm = 0.2 * np.random.default_rng(0).standard_normal(sr * 4)
        self.assertLessEqual(balance.cold_open(pcm, sr)["onset_s"], 0.05)


class NewChecks(unittest.TestCase):
    def test_opening_check(self):
        self.assertEqual(grade_checks.check_opening({"opening": {"ok": False, "detail": "frozen"}})["status"], "fail")
        self.assertEqual(grade_checks.check_opening({"opening": {"ok": True, "detail": "fine"}})["status"], "pass")
        self.assertEqual(grade_checks.check_opening({})["status"], "warn")

    def test_cold_open_check(self):
        self.assertEqual(grade_checks.check_cold_open({"cold_open": {"onset_s": 0.0, "first_db": -20, "median_db": -22}})["status"], "pass")
        self.assertEqual(grade_checks.check_cold_open({"cold_open": {"onset_s": 1.2, "first_db": -70, "median_db": -22}})["status"], "fail")

    def test_ending_check(self):
        base = {"video": {"duration": 30.0}, "render": {"trim": {"end_reason": "payoff"}}}
        self.assertEqual(grade_checks.check_ending({**base, "word_spans": [[1, 2], [29.0, 29.7]]})["status"], "pass")
        self.assertEqual(grade_checks.check_ending({**base, "word_spans": [[1, 2], [29.5, 30.3]]})["status"], "fail")
        self.assertEqual(grade_checks.check_ending({**base, "word_spans": [[1, 2], [20.0, 21.0]]})["status"], "warn")

    def test_hook_match_check(self):
        mk = lambda h: {"render": {"hook": h}}
        self.assertEqual(grade_checks.check_hook_match(mk({"text": "x y", "source": "given", "match": True}))["status"], "pass")
        self.assertEqual(grade_checks.check_hook_match(mk({"text": "x y", "source": "given_unverified", "match": False}))["status"], "warn")


if __name__ == "__main__":
    unittest.main()
