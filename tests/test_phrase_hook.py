"""A1 hook + captions: the hook is one complete phrase, never a split or copy of caption 1; every word stays captioned."""
import unittest

from clip_engine import captions, hooks, render_clip as rc


def W(text, t0=0.0, step=0.3):
    out, t = [], t0
    for x in text.split():
        out.append({"w": x, "s": round(t, 3), "e": round(t + step - 0.04, 3), "p": 0.95})
        t += step
    return out


SPEECH = ("Okay so we just got to the vault. Chat I am not going to lie, this door is huge. "
          "Wait is that a boss? No way he is behind it!")


class PhraseHook(unittest.TestCase):
    def setUp(self):
        self.ws = W(SPEECH)

    def test_hook_is_a_whole_sentence_not_a_piece_of_caption_one(self):
        first = hooks.first_line_text(self.ws)                  # "Okay so we just"
        h, src, ok = hooks.phrase_hook("", self.ws)
        self.assertTrue(h)
        self.assertTrue(ok)
        self.assertLessEqual(len(h.split()), 6)
        self.assertTrue(hooks.is_complete(h))
        self.assertFalse(hooks.duplicates(h, first))
        self.assertNotIn(h.lower(), first.lower())
        # it is a real sentence or clause of the speech: starts after a boundary, ends on a boundary
        starts = {" ".join(w["w"] for w in c) for s in hooks.sentences(self.ws) for c in hooks.clauses(s)}
        self.assertIn(h, {x.rstrip(".,") for x in starts} | starts)

    def test_hook_never_ends_mid_thought(self):
        h, _, _ = hooks.phrase_hook("", self.ws)
        self.assertNotIn(hooks.last_token(h), hooks.DANGLING)

    def test_given_hook_kept_when_spoken_and_not_caption_one(self):
        h, src, ok = hooks.phrase_hook("No way he is behind it!", self.ws)
        self.assertEqual((h, src, ok), ("No way he is behind it!", "given", True))

    def test_given_hook_that_copies_caption_one_is_replaced(self):
        h, src, ok = hooks.phrase_hook("Okay so we just got", self.ws)
        self.assertNotEqual(src, "given")
        self.assertFalse(hooks.duplicates(h, hooks.first_line_text(self.ws)))

    def test_given_hook_cut_mid_thought_is_replaced(self):
        h, src, _ = hooks.phrase_hook("so I was going to", self.ws)
        self.assertNotEqual(h, "so I was going to")
        self.assertTrue(hooks.is_complete(h))

    def test_opening_sentence_that_is_caption_one_is_not_the_hook(self):
        ws = W("Bro give me that. Right now please, I mean it!")
        h, _, _ = hooks.phrase_hook("", ws)
        self.assertFalse(hooks.duplicates(h, hooks.first_line_text(ws)))
        self.assertNotEqual(h.lower().rstrip("."), "bro give me that")

    def test_no_candidate_gives_no_hook_instead_of_a_garbled_one(self):
        h, src, ok = hooks.phrase_hook("", W("uh"))
        self.assertEqual((h, src, ok), ("", "none", False))


class EveryWordCaptioned(unittest.TestCase):
    WORDS = W(SPEECH)

    def test_ass_has_every_spoken_word_in_order(self):
        ass, lane, (hook, src, match) = rc.build_ass(self.WORDS, "", True, (0.0, 0.4, 0.2, 0.2))
        spoken = [w["w"] for w in self.WORDS]
        shown = " ".join(captions.plain_lines(ass)).split()
        self.assertEqual(shown, spoken)                         # nothing dropped for the hook, nothing added
        self.assertTrue(hook)
        self.assertFalse(hooks.duplicates(hook, captions.plain_lines(ass)[0]))

    def test_hook_words_are_not_removed_from_the_captions(self):
        ass, _, (hook, _, _) = rc.build_ass(self.WORDS, "", True, (0.0, 0.4, 0.2, 0.2))
        shown = " ".join(captions.plain_lines(ass)).lower()
        for t in captions._tok(hook):
            self.assertIn(t, shown)

    def test_one_style_and_at_most_one_highlight_per_line(self):
        ass, _, _ = rc.build_ass(self.WORDS, "", True, (0.0, 0.4, 0.2, 0.2))
        rows = [l for l in ass.splitlines() if l.startswith("Dialogue:") and ",Main," in l]
        self.assertTrue(rows)
        self.assertTrue(all(l.count(captions.HL_ON) <= 1 for l in rows))


if __name__ == "__main__":
    unittest.main()
