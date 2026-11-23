"""captions_pass end to end on injected audio / VAD / Gemini / OCR, and the grader check that reads its numbers."""
import unittest

import numpy as np

from clip_engine import caption_sources as cs
from clip_engine import captions_pass
from clip_engine import grade_checks as gc


def W(text, t0, step=0.32, p=0.95):
    out, t = [], t0
    for x in text.split():
        out.append({"w": x, "s": round(t, 3), "e": round(t + step - 0.04, 3), "p": p})
        t += step
    return out


class Pipeline(unittest.TestCase):
    SPEECH = "okay so we just got to the vault and chat this door is huge"

    def run_pass(self, words, spans, gtext, rescue_words=None):
        model_fn = (lambda: (lambda audio: rescue_words)) if rescue_words is not None else None
        return captions_pass.run("x.mp4", 0.0, 5.0, words, 1920, 1080, model_fn=model_fn, key="k",
                                 pcm=np.zeros(16000 * 6, dtype=np.float32), spans=spans,
                                 gemini_fn=lambda p, k: (gtext, None), ocr_fn=lambda g: [], ocr_frames=[],
                                 log=lambda *_: None)

    def test_vad_missed_words_are_rescued_and_the_gate_passes(self):
        full = W(self.SPEECH, 0.2)
        kept = full[:5] + full[8:]                              # 'got to the' never made it into the words
        spans = [(0.2, full[-1]["e"])]
        shift = max(0.0, kept[4]["e"] + 0.10 - cs.RESCUE_CTX)   # the rescue audio starts ctx before the chunk
        miss = [{"w": w["w"], "s": w["s"] - shift, "e": w["e"] - shift, "p": 0.9} for w in full[5:8]]
        final, chk = self.run_pass(kept, spans, self.SPEECH, rescue_words=miss)
        self.assertEqual([w["w"] for w in final], self.SPEECH.split())
        self.assertGreaterEqual(chk["coverage"], 0.9)
        self.assertTrue(chk["gate"]["ok"])
        self.assertEqual(chk["rescue"]["added"], 3)

    def test_without_the_rescue_the_gate_fails_on_coverage(self):
        full = W(self.SPEECH, 0.2)
        final, chk = self.run_pass(full[:3] + full[9:], [(0.2, full[-1]["e"])], None)
        self.assertFalse(chk["gate"]["ok"])
        self.assertLess(chk["coverage"], 0.9)

    def test_unmasked_curse_stays_matchable_for_the_mask(self):
        final, chk = self.run_pass(W("holy fuck this is huge", 0.2), [(0.2, 1.9)], "holy f*** this is huge")
        self.assertEqual([w["w"] for w in final], ["holy", "fuck", "this", "is", "huge"])
        self.assertEqual(chk["counts"]["corrected"], 0)

    def test_summary_has_what_the_grader_reads(self):
        full = W(self.SPEECH, 0.2)
        _, chk = self.run_pass(full, [(0.2, full[-1]["e"])], self.SPEECH)
        s = captions_pass.summary(chk)
        self.assertTrue(s["gemini"]["available"])
        self.assertIn("coverage", s)
        self.assertEqual(s["unresolved"], [])


class GradeCheck(unittest.TestCase):
    def m(self, **cc):
        base = {"coverage": 0.95, "speech_sec": 20.0, "second_source": True, "unresolved": [],
                "counts": {"corrected": 0, "inserted": 0, "unresolved": 0}, "gemini": {"available": True}, "ocr": {}}
        base.update(cc)
        return {"render": {"captions_check": base}}

    def test_pass(self):
        self.assertEqual(gc.check_caption_words(self.m())["status"], "pass")

    def test_low_coverage_fails(self):
        c = gc.check_caption_words(self.m(coverage=0.82))
        self.assertEqual(c["status"], "fail")
        self.assertIn("82%", c["detail"])

    def test_unresolved_fails(self):
        self.assertEqual(gc.check_caption_words(self.m(unresolved=[{"i": 3, "why": "x"}]))["status"], "fail")

    def test_missing_measurement_fails(self):
        self.assertEqual(gc.check_caption_words({"render": {}})["status"], "fail")

    def test_no_second_source_is_a_warning_not_a_pass(self):
        self.assertEqual(gc.check_caption_words(self.m(second_source=False))["status"], "warn")
