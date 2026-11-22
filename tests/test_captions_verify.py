"""Caption verification on real-looking transcripts: wrong word, missing word, OCR over Gemini, coverage gate."""
import unittest

from clip_engine import captions_verify as cv


def W(text, t0, step=0.32, p=0.95):
    out, t = [], t0
    for x in text.split():
        out.append({"w": x, "s": round(t, 3), "e": round(t + step - 0.04, 3), "p": p})
        t += step
    return out


SPEECH = "okay so we just got to the vault and chat I am not going to lie this door is huge"
SPANS = [(0.0, 4.9)]


class Verify(unittest.TestCase):
    def test_all_agree(self):
        base = W(SPEECH, 0.1)
        final, chk = cv.verify(base, SPEECH, None, [(0.1, 0.1 + 0.32 * len(base))])
        self.assertEqual([w["w"] for w in final], SPEECH.split())
        self.assertTrue(chk["gate"]["ok"])
        self.assertEqual(chk["counts"]["corrected"], 0)
        self.assertTrue(chk["second_source"])

    def test_wrong_word_low_confidence_is_corrected_by_gemini(self):
        base = W(SPEECH, 0.1)
        base[12]["w"], base[12]["p"] = "naughty", 0.41          # 'not' heard as 'naughty'
        final, chk = cv.verify(base, SPEECH, None, [(0.1, 4.9)])
        self.assertEqual(final[12]["w"], "not")
        self.assertEqual(chk["corrections"][0]["from"], "naughty")
        self.assertEqual(chk["corrections"][0]["source"], "gemini")
        self.assertTrue(chk["gate"]["ok"])

    def test_confident_base_is_kept_against_lone_gemini(self):
        base = W(SPEECH, 0.1)
        g = SPEECH.replace("this door", "this floor")
        final, chk = cv.verify(base, g, None, [(0.1, 4.9)])
        self.assertEqual(final[17]["w"], "door")
        self.assertEqual(chk["counts"]["kept_confident"], 1)
        self.assertTrue(chk["gate"]["ok"])

    def test_spelling_level_difference_keeps_base(self):
        base = W("hey Lucia look at this", 0.1)
        final, chk = cv.verify(base, "hey Lusia look at this", None, [(0.1, 1.8)])
        self.assertEqual(final[1]["w"], "Lucia")
        self.assertEqual(chk["counts"]["corrected"], 0)

    def test_missing_word_is_inserted_into_the_speech_gap(self):
        base = W("we just got to the vault", 0.0)
        del_idx = 3
        gap_start = base[del_idx - 1]["e"]
        base = base[:del_idx] + base[del_idx + 1:]            # 'to' was dropped by the VAD
        spans = [(0.0, 2.0)]
        final, chk = cv.verify(base, "we just got to the vault", None, spans)
        self.assertEqual([w["w"] for w in final], "we just got to the vault".split())
        ins = [w for w in final if w.get("inserted")]
        self.assertEqual(len(ins), 1)
        self.assertGreaterEqual(ins[0]["s"], gap_start - 1e-6)
        self.assertLessEqual(ins[0]["e"], base[del_idx]["s"] + 1e-6)
        self.assertEqual(chk["counts"]["inserted"], 1)

    def test_insert_without_speech_is_rejected_not_captioned(self):
        base = W("we just got there", 0.0) + W("vault", 5.0)
        final, chk = cv.verify(base, "we just got there thanks vault", None, [(0.0, 1.2), (5.0, 5.3)])
        self.assertNotIn("thanks", [w["w"] for w in final])
        self.assertEqual(chk["counts"]["rejected_inserts"], 1)
        self.assertTrue(chk["gate"]["ok"])

    def test_ocr_wins_for_scripted_game_dialogue(self):
        base = W("the Kaylee garden is this way", 10.0)
        base[1]["p"] = 0.9
        ocr = [{"s": 10.0, "e": 11.8, "text": "The Caelid garden is this way"}]
        final, chk = cv.verify(base, "the Kaylee garden is this way", ocr, [(10.0, 11.8)])
        self.assertEqual(final[1]["w"], "Caelid")
        self.assertEqual(chk["corrections"][0]["source"], "ocr")
        self.assertTrue(chk["sources"]["ocr"]["available"])

    def test_ocr_line_that_is_not_this_speech_is_ignored(self):
        base = W("we just got to the vault", 0.0)
        ocr = [{"s": 0.0, "e": 2.0, "text": "SUBSCRIBE 1000 GOAL RAID ALERT"}]
        final, chk = cv.verify(base, "we just got to the vault", ocr, [(0.0, 2.0)])
        self.assertEqual([w["w"] for w in final], "we just got to the vault".split())
        self.assertFalse(chk["sources"]["ocr"]["available"])

    def test_three_way_disagreement_is_unresolved_and_fails_the_gate(self):
        base = W("open the red gate now", 0.0)
        ocr = [{"s": 0.0, "e": 1.6, "text": "open the blue gate now"}]
        final, chk = cv.verify(base, "open the bed gate now", ocr, [(0.0, 1.6)])
        self.assertEqual(chk["counts"]["unresolved"], 1)
        self.assertFalse(chk["gate"]["ok"])
        self.assertIn("unresolved", chk["gate"]["reasons"][0])

    def test_coverage_gate_counts_speech_seconds(self):
        base = W("we just got there", 0.0)                      # captions end at ~1.3 s
        _, chk = cv.verify(base, "we just got there", None, [(0.0, 6.0)])
        self.assertLess(chk["coverage"], 0.9)
        self.assertFalse(chk["gate"]["ok"])
        self.assertIn("cover", chk["gate"]["reasons"][0])

    def test_half_the_words_missing_is_rescued_to_full_coverage(self):
        words = SPEECH.split()
        base = W(SPEECH, 0.0)
        kept = [w for i, w in enumerate(base) if i % 2 == 0]    # the old failure: only half captioned
        _, bad = cv.verify(kept, None, None, [(0.0, 0.32 * len(words))])
        self.assertFalse(bad["gate"]["ok"])
        final, chk = cv.verify(kept, SPEECH, None, [(0.0, 0.32 * len(words))])
        self.assertEqual(len(final), len(words))
        self.assertTrue(chk["gate"]["ok"], chk["gate"])

    def test_filler_words_do_not_create_disagreement(self):
        base = W("so we go in", 0.0)
        final, chk = cv.verify(base, "um so we go in uh", None, [(0.0, 1.5)])
        self.assertEqual(len(final), 5 - 1)
        self.assertEqual(chk["counts"]["unresolved"], 0)

    def test_no_second_source_is_reported_not_hidden(self):
        base = W("we just got there", 0.0)
        final, chk = cv.verify(base, None, None, [(0.0, 1.3)], gemini_error="HTTP 503")
        self.assertFalse(chk["second_source"])
        self.assertEqual(chk["sources"]["gemini"]["error"], "HTTP 503")
        self.assertEqual(chk["counts"]["single_source"], 4)

    def test_profanity_star_words_match_their_spelled_form(self):
        self.assertTrue(cv.same("f***ing", "fucking"))
        base = W("that was f***ing huge", 0.0)
        final, chk = cv.verify(base, "that was fucking huge", None, [(0.0, 1.4)])
        self.assertEqual(final[2]["w"], "f***ing")
        self.assertEqual(chk["counts"]["corrected"], 0)

    def test_punctuation_and_case_of_the_base_are_kept_on_correction(self):
        base = W("Yeah, naughty. Okay", 0.0)
        base[1]["p"] = 0.3
        final, _ = cv.verify(base, "Yeah not okay", None, [(0.0, 1.0)])
        self.assertEqual(final[1]["w"], "not.")


if __name__ == "__main__":
    unittest.main()
