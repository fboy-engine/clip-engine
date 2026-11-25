"""Hook without Gemini: a deterministic local phrase from unpunctuated speech, and never an unsafe word."""
import unittest

from clip_engine import hooks


def W(text, t0=0.0, step=0.3):
    out, t = [], t0
    for x in text.split():
        out.append({"w": x, "s": round(t, 3), "e": round(t + step - 0.04, 3), "p": 0.95})
        t += step
    return out


# Golden, local ASR: lowercase, no punctuation, no pauses (the run that came out with no hook at all)
GOLDEN = ("i've never seen a man shout as well before wait this is actually going to teach the public how to wash "
          "themselves no they're not because they won't move the stick around guys we're meant to be")


class LocalHook(unittest.TestCase):
    def test_unpunctuated_speech_still_gets_a_hook(self):
        ws = W(GOLDEN)
        h, src, ok = hooks.phrase_hook("", ws)
        self.assertTrue(h, "no hook from continuous unpunctuated speech")
        self.assertEqual(src, "speech_local")
        self.assertTrue(ok)
        self.assertTrue(3 <= len(h.split()) <= hooks.MAX_WORDS)
        self.assertTrue(hooks.is_complete(h))

    def test_hook_is_spoken_in_order_and_not_caption_one(self):
        ws = W(GOLDEN)
        h, _, _ = hooks.phrase_hook("", ws)
        spoken = " ".join(w["w"] for w in ws).lower()
        self.assertIn(h.lower(), spoken)
        self.assertFalse(hooks.duplicates(h, hooks.first_line_text(ws)))

    def test_same_words_same_hook(self):
        a = hooks.phrase_hook("", W(GOLDEN))
        b = hooks.phrase_hook("", W(GOLDEN))
        self.assertEqual(a, b)

    def test_never_starts_on_a_connector_or_ends_dangling(self):
        h, _, _ = hooks.phrase_hook("", W(GOLDEN))
        self.assertNotIn(hooks.last_token(h), hooks.DANGLING)
        self.assertNotIn(hooks._tok(h)[0], hooks.FRAGMENT_START)

    def test_too_little_speech_gives_no_hook(self):
        self.assertEqual(hooks.phrase_hook("", W("uh yeah"))[:2], ("", "none"))


class UnsafeWords(unittest.TestCase):
    BAD = "chat is scrubbing bum crack gay"

    def test_unsafe_detects_flaggable_words(self):
        for t in ("chat is scrubbing bum crack gay", "that was so gay", "what the f***", "no way crack"):
            self.assertTrue(hooks.unsafe(t), t)
        for t in ("no way he is behind it", "move the stick around", "the car is about to blow up"):
            self.assertFalse(hooks.unsafe(t), t)

    def test_unsafe_line_is_never_the_hook(self):
        ws = W("okay " + self.BAD + " and then we drive to the bank")
        h, src, _ = hooks.phrase_hook("", ws)
        self.assertFalse(hooks.unsafe(h), h)

    def test_unsafe_given_hook_is_replaced(self):
        ws = W("Okay so we got to the vault. No way he is behind it!")
        h, src, _ = hooks.phrase_hook("so gay lol", ws)
        self.assertNotEqual(src, "given")
        self.assertFalse(hooks.unsafe(h))

    def test_all_unsafe_speech_gives_no_hook(self):
        h, src, _ = hooks.phrase_hook("", W("gay gay crack crack bum bum"))
        self.assertEqual((h, src), ("", "none"))


if __name__ == "__main__":
    unittest.main()
