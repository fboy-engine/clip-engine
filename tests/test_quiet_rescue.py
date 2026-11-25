import unittest

import numpy as np

from clip_engine import quiet_rescue as qr

SR = 16000


def W(w, s, e, p=0.9):
    return {"w": w, "s": s, "e": e, "p": p}


class Dsp(unittest.TestCase):
    def test_highpass_removes_low_keeps_high(self):
        t = np.arange(SR) / SR
        low, high = np.sin(2 * np.pi * 60 * t), np.sin(2 * np.pi * 1000 * t)
        y = qr.highpass(low + high, 200.0)
        self.assertLess(np.abs(np.fft.rfft(y)[60]) / np.abs(np.fft.rfft(low)[60]), 0.01)
        self.assertGreater(np.abs(np.fft.rfft(y)[1000]) / np.abs(np.fft.rfft(high)[1000]), 0.95)

    def test_normalise_levels_quiet_and_keeps_silence(self):
        x = (0.01 * np.random.RandomState(0).randn(SR)).astype(np.float32)
        y = qr.normalise(x)
        self.assertAlmostEqual(20 * np.log10(np.sqrt((y.astype(float) ** 2).mean())), -20.0, delta=1.0)
        self.assertLessEqual(float(np.abs(y).max()), 0.95 + 1e-6)
        self.assertEqual(float(np.abs(qr.normalise(np.zeros(100))).max()), 0.0)


class Agree(unittest.TestCase):
    def test_two_passes_agree_word_is_taken_with_earliest_time(self):
        p = {"hp200": [W("slow", 5.0, 5.3, 0.7)], "hp300": [W("slow", 5.1, 5.4, 0.6)]}
        out, rows = qr.agree(p, 4.0, 7.0, [])
        self.assertEqual([(w["w"], w["s"]) for w in out], [("slow", 5.0)])
        self.assertTrue(rows[0]["taken"])

    def test_one_pass_only_is_not_taken_and_is_reported(self):
        out, rows = qr.agree({"hp200": [W("nice", 5.0, 5.3, 0.9)], "hp300": []}, 4.0, 7.0, [])
        self.assertEqual(out, [])
        self.assertFalse(rows[0]["taken"])

    def test_unsure_pass_does_not_count(self):
        p = {"hp200": [W("nice", 5.0, 5.3, 0.9)], "hp300": [W("nice", 5.0, 5.3, 0.3)]}
        self.assertEqual(qr.agree(p, 4.0, 7.0, [])[0], [])

    def test_different_words_or_far_apart_do_not_agree(self):
        self.assertEqual(qr.agree({"a": [W("nice", 5.0, 5.3)], "b": [W("mice", 5.0, 5.3)]}, 4, 7, [])[0], [])
        self.assertEqual(qr.agree({"a": [W("nice", 5.0, 5.3)], "b": [W("nice", 6.0, 6.3)]}, 4, 7, [])[0], [])

    def test_outside_stretch_and_on_existing_word_refused(self):
        p = {"a": [W("go", 9.0, 9.3)], "b": [W("go", 9.0, 9.3)]}
        self.assertEqual(qr.agree(p, 4.0, 7.0, [])[0], [])
        p = {"a": [W("go", 5.0, 5.3)], "b": [W("go", 5.0, 5.3)]}
        self.assertEqual(qr.agree(p, 4.0, 7.0, [W("go", 5.0, 5.4)])[0], [])


class Run(unittest.TestCase):
    def test_run_offsets_back_onto_clip_clock_and_asks_every_variant(self):
        pcm = np.zeros(10 * SR, dtype=np.float32)
        calls = []

        def fn(audio):
            calls.append(len(audio) / SR)
            return [W("slow", 0.8, 1.1, 0.8)]       # 0.8 s into a stretch that starts at 3.5 - 0.5 = 3.0
        out, info = qr.run(pcm, [(3.5, 5.0)], [], fn)
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(calls[0], 2.5, places=3)
        self.assertEqual([(w["w"], w["s"]) for w in out], [("slow", 3.8)])
        self.assertEqual(info["added"], 1)


if __name__ == "__main__":
    unittest.main()


class InPass(unittest.TestCase):
    def test_pass_adds_quiet_word_two_passes_agree_and_reports(self):
        from clip_engine import captions_pass as cp
        pcm = (0.3 * np.random.RandomState(1).randn(12 * SR)).astype(np.float32)
        base = [W("hello", 0.5, 1.0), W("there", 1.1, 1.6), W("end", 10.0, 10.5)]
        spans = [(0.4, 1.7), (4.0, 5.2), (9.9, 10.6)]

        def model_fn():
            def fn(audio):
                dur = len(audio) / SR
                if abs(dur - 2.2) < 0.05:                     # the 4.0-5.2 stretch +-0.5 s (VAD-off whole-window pass is longer)
                    return [W("slow", 0.9, 1.2, 0.8)]
                return []
            return fn
        final, check = cp.run("x", 0.0, 12.0, base, 1920, 1080, model_fn=model_fn, pcm=pcm, spans=spans,
                              gemini_fn=lambda pcm, key: (None, "off"), ocr_fn=lambda *a, **k: [], ocr_frames=[])
        self.assertIn("slow", [w["w"] for w in final])
        self.assertEqual(check["quiet_rescue"]["added"], 1)
        self.assertEqual(check["retime"]["moved"], 0)
