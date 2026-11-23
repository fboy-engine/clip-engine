"""speech_spans: which VAD seconds count as speech seconds for the caption gate (synthetic audio, no models)."""
import unittest

import numpy as np

from clip_engine import captions_pass
from clip_engine import speech_spans as ss

SR = ss.SR


def tone(sec, rhythm=True, amp=0.1):
    """Voiced-like sound: 150 Hz + harmonics; rhythm=True moves the loudness 4x per second (syllables)."""
    t = np.arange(int(sec * SR)) / SR
    x = sum(np.sin(2 * np.pi * 150 * k * t) / k for k in (1, 2, 3))
    env = (0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)) if rhythm else 1.0
    return (amp * x * env / 2).astype(np.float32)


def silence(sec):
    return np.zeros(int(sec * SR), dtype=np.float32)


def word(s, e):
    return {"w": "x", "s": s, "e": e, "p": 0.9}


class Silence(unittest.TestCase):
    def test_finds_digital_silence_only_when_long_enough(self):
        pcm = np.concatenate([tone(1.0), silence(0.5), tone(1.0), silence(0.1), tone(1.0)])
        got = ss.digital_silence(pcm)
        self.assertEqual(len(got), 1)
        self.assertAlmostEqual(got[0][0], 1.0, delta=0.03)
        self.assertAlmostEqual(got[0][1], 1.5, delta=0.03)

    def test_quiet_speech_is_not_silence(self):
        self.assertEqual(ss.digital_silence(tone(2.0, amp=0.004)), [])      # about -50 dBFS

    def test_silence_inside_a_span_is_excluded_and_logged(self):
        pcm = np.concatenate([tone(2.0), silence(1.5), tone(2.0)])
        spans, ex, info = ss.refine(pcm, [(0.0, 5.5)], [word(0.1, 1.9), word(3.6, 5.4)])
        self.assertEqual(len(ex), 1)
        self.assertIn("digital silence", ex[0]["reason"])
        self.assertAlmostEqual(ex[0]["start"], 2.0, delta=0.15)
        self.assertAlmostEqual(ex[0]["end"], 3.5, delta=0.15)
        self.assertLess(info["counted_speech_sec"], info["vad_speech_sec"] - 1.0)

    def test_a_word_on_digital_silence_is_never_excluded(self):
        pcm = np.concatenate([tone(2.0), silence(1.5), tone(2.0)])
        spans, ex, _ = ss.refine(pcm, [(0.0, 5.5)], [word(0.1, 1.9), word(2.3, 3.2), word(3.6, 5.4)])
        for r in ex:
            self.assertFalse(2.2 < r["start"] < 3.3 and 2.2 < r["end"] < 3.3, r)    # nothing inside the word span
        self.assertTrue(any(a <= 2.5 and b >= 3.0 for a, b in spans))


class NonWordSound(unittest.TestCase):
    def test_steady_sound_without_a_word_is_excluded(self):
        pcm = np.concatenate([tone(2.0), tone(1.5, rhythm=False), tone(2.0)])
        _, ex, _ = ss.refine(pcm, [(0.0, 5.5)], [word(0.1, 1.9), word(3.6, 5.4)])
        self.assertEqual(len(ex), 1)
        self.assertIn("steady voiced sound", ex[0]["reason"])
        self.assertAlmostEqual(ex[0]["start"], 2.0, delta=0.2)

    def test_the_same_sound_with_a_word_on_it_stays(self):
        pcm = np.concatenate([tone(2.0), tone(1.5, rhythm=False), tone(2.0)])
        _, ex, _ = ss.refine(pcm, [(0.0, 5.5)], [word(0.1, 1.9), word(2.1, 3.4), word(3.6, 5.4)])
        self.assertEqual(ex, [])

    def test_voiced_sound_with_syllable_rhythm_stays_counted(self):
        pcm = np.concatenate([tone(2.0), tone(1.5, rhythm=True), tone(2.0)])
        _, ex, _ = ss.refine(pcm, [(0.0, 5.5)], [word(0.1, 1.9), word(3.6, 5.4)])
        self.assertEqual(ex, [])

    def test_a_stretch_too_short_to_judge_stays_counted(self):
        pcm = np.concatenate([tone(2.0), tone(0.4, rhythm=False), tone(2.0)])
        _, ex, _ = ss.refine(pcm, [(0.0, 4.4)], [word(0.1, 1.9), word(2.5, 4.3)])
        self.assertEqual(ex, [])

    def test_modulation_none_when_too_short(self):
        self.assertIsNone(ss.syllable_modulation(tone(0.3)))

    def test_every_excluded_stretch_is_logged_with_numbers(self):
        pcm = np.concatenate([tone(2.0), silence(1.0), tone(1.5, rhythm=False), tone(2.0)])
        _, ex, info = ss.refine(pcm, [(0.0, 6.5)], [word(0.1, 1.9), word(4.6, 6.4)])
        self.assertEqual(len(ex), 2)
        for r in ex:
            self.assertEqual(set(r), {"start", "end", "sec", "reason"})
        self.assertAlmostEqual(info["excluded_sec"], sum(r["sec"] for r in ex), places=2)


class BackstopAndGate(unittest.TestCase):
    def run_pass(self, pcm, words, dur):
        return captions_pass.run("x.mp4", 0.0, dur, words, 1920, 1080, model_fn=None, key="k", pcm=pcm,
                                 spans=[(0.0, dur)], gemini_fn=lambda p, k: (None, "off"),
                                 ocr_fn=lambda g: [], ocr_frames=[], log=lambda *_: None)

    def test_exclusions_cannot_hide_a_clip_that_is_mostly_uncaptioned(self):
        pcm = np.concatenate([tone(2.0), tone(8.0, rhythm=False)])           # 2 s of words, 8 s of steady sound
        final, chk = self.run_pass(pcm, [word(0.1, 1.9)], 10.0)
        self.assertGreaterEqual(chk["coverage"], 0.9)                         # after the exclusion it looks fine
        self.assertLess(chk["speech"]["raw_window_coverage"], ss.MIN_RAW_COVERAGE)
        self.assertFalse(chk["gate"]["ok"])
        self.assertTrue(any("no exclusions" in r for r in chk["gate"]["reasons"]))

    def test_gate_passes_when_words_carry_the_clip_and_a_pause_is_dropped(self):
        pcm = np.concatenate([tone(4.0), silence(1.0), tone(4.0)])
        words = [word(0.1, 3.9), word(5.1, 8.9)]
        final, chk = self.run_pass(pcm, words, 9.0)
        self.assertTrue(chk["gate"]["ok"], chk["gate"])
        self.assertEqual(len(chk["speech"]["excluded"]), 1)
        self.assertGreaterEqual(chk["speech"]["raw_window_coverage"], 0.75)

    def test_summary_carries_the_excluded_stretches(self):
        pcm = np.concatenate([tone(4.0), silence(1.0), tone(4.0)])
        _, chk = self.run_pass(pcm, [word(0.1, 3.9), word(5.1, 8.9)], 9.0)
        s = captions_pass.summary(chk)
        self.assertEqual(len(s["speech"]["excluded"]), 1)


if __name__ == "__main__":
    unittest.main()
