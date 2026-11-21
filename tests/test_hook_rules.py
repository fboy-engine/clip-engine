"""Hook rules: at most 6 words, complete, never a repeat of the first caption line; profanity masked."""
import unittest

from clip_engine import asr_fix, captions, grade_checks as gc, hooks


def W(w, s, e):
    return {"w": w, "s": s, "e": e, "p": 0.9}


class Complete(unittest.TestCase):
    def test_dangling_end_is_not_complete(self):
        self.assertFalse(hooks.is_complete("what you lock the"))
        self.assertFalse(hooks.is_complete("so I was going to"))
        self.assertTrue(hooks.is_complete("bro give me that"))
        self.assertFalse(hooks.is_complete("wait"))                      # one word
        self.assertFalse(hooks.is_complete("one two three four five six seven"))

    def test_trim_dangling(self):
        self.assertEqual(hooks.trim_dangling(["lock", "the"]), ["lock"])
        self.assertEqual(hooks.trim_dangling(["go", "to", "the"]), ["go"])
        self.assertEqual(hooks.trim_dangling(["bro", "give", "me"]), ["bro", "give", "me"])

    def test_spoken_hook_never_ends_mid_thought(self):
        ws = [W("what", 0.0, .2), W("you", .2, .4), W("doing", .4, .7), W("with", .7, .9), W("the", .9, 1.0)]
        text, src, ok = hooks.choose_hook("", ws)
        self.assertEqual(src, "speech_first3s")
        self.assertTrue(hooks.is_complete(text))
        self.assertLessEqual(len(text.split()), 6)


class Duplicates(unittest.TestCase):
    def test_same_words_duplicate(self):
        self.assertTrue(hooks.duplicates("bro give me that", "bro give me that"))
        self.assertTrue(hooks.duplicates("bro give me that", "Bro, give me"))
        self.assertFalse(hooks.duplicates("bro give me that", "right now"))

    def test_hook_words_leave_the_captions(self):
        ws = [W("bro", 0.0, .3), W("give", .4, .7), W("me", .8, 1.0), W("that", 1.1, 1.4), W("right", 2.0, 2.3),
              W("now", 2.4, 2.7), W("bro", 5.0, 5.2)]
        cap = captions.drop_hook_words(ws, "bro give me that", 2.6)
        self.assertEqual([w["w"] for w in cap], ["right", "now", "bro"])        # a later "bro" stays
        ass = captions.to_ass(cap, 0.0, 1080, 1920, 420, 540)
        self.assertFalse(hooks.duplicates("bro give me that", captions.plain_lines(ass)[0]))

    def test_unrelated_words_are_kept(self):
        ws = [W("hello", 0.0, .3), W("world", .4, .7)]
        self.assertEqual(captions.drop_hook_words(ws, "bro give me that", 2.6), ws)

    def test_grade_fails_a_repeat_and_a_long_hook(self):
        base = {"hook_start": 0.0, "hook_text": "bro give me that", "first_caption": "bro give me that",
                "caption_styles": ["Main"], "caption_lines": 3, "max_highlights": 1, "last_end": 10.0}
        self.assertEqual(gc.check_hook({"ass": base})["status"], gc.FAIL)
        ok = {**base, "first_caption": "right now bro"}
        self.assertEqual(gc.check_hook({"ass": ok})["status"], gc.PASS)
        long_ = {**ok, "hook_text": "one two three four five six seven"}
        self.assertEqual(gc.check_hook({"ass": long_})["status"], gc.FAIL)
        cut = {**ok, "hook_text": "what you lock the"}
        self.assertEqual(gc.check_hook({"ass": cut})["status"], gc.FAIL)


class Profanity(unittest.TestCase):
    def test_not_masked_inside_other_words(self):
        for w in ("duck", "classic", "Scunthorpe", "assist", "shitake"[:0] + "hello"):
            self.assertEqual(asr_fix.mask_word(w), w)

    def test_text_mask_and_family(self):
        self.assertEqual(asr_fix.mask_text("what the fuck bro"), "what the f*** bro")
        self.assertEqual(asr_fix.mask_word("bullshit."), "b*******.")
        self.assertEqual(asr_fix.mask_word("Motherfucker!"), "M***********!")

    def test_grade_flags_unmasked_words_and_dejavu(self):
        a = {"caption_styles": ["Main"], "caption_lines": 2, "max_highlights": 1, "last_end": 3.0,
             "fonts": ["Anton"], "unmasked_profanity": ["fuck"]}
        self.assertEqual(gc.check_captions({"ass": a, "video": {"duration": 30}})["status"], gc.FAIL)
        a2 = {**a, "unmasked_profanity": [], "fonts": ["DejaVu Sans"]}
        self.assertEqual(gc.check_captions({"ass": a2, "video": {"duration": 30}})["status"], gc.FAIL)


class Reference(unittest.TestCase):
    def test_reference_that_is_not_this_speech_changes_nothing(self):
        ws = [W("hello", 0, .4), W("there", .5, .9), W("friend", 1, 1.4)]
        out, n = asr_fix.apply_reference(ws, "completely different subtitle text here now")
        self.assertEqual((out, n), (ws, 0))

    def test_punctuation_is_kept(self):
        ws = [W("Female,", 0, .4), W("muscular", .5, 1.0), W("in", 1.1, 1.2), W("her", 1.2, 1.3), W("20s.", 1.3, 1.6)]
        out, n = asr_fix.apply_reference(ws, "Famous muscular in her 20s")
        self.assertEqual(out[0]["w"], "Famous,")
        self.assertEqual(n, 1)


class Fonts(unittest.TestCase):
    def test_bundled_font_exists_and_is_not_dejavu(self):
        import os
        self.assertTrue(os.path.isfile(os.path.join(captions.FONTS_DIR, "Anton-Regular.ttf")))
        self.assertTrue(os.path.isfile(os.path.join(captions.FONTS_DIR, "OFL.txt")))
        self.assertEqual(captions.FONT, "Anton")
        self.assertEqual(captions.ass_fonts(captions.to_ass([W("hi", 0, 1)])), ["Anton"])


if __name__ == "__main__":
    unittest.main()
