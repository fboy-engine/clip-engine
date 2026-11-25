import unittest

from clip_engine import word_retime as wr


def W(w, s, e, p=0.9):
    return {"w": w, "s": s, "e": e, "p": p}


class WordRetime(unittest.TestCase):
    def test_late_word_moves_to_heard_time(self):
        words = [W("so", 10.0, 10.3), W("chat", 15.48, 15.9), W("is", 16.0, 16.2)]
        heard = [W("chat", 13.8, 14.2, 0.79)]
        out, mv = wr.retime(words, heard, [(13.7, 14.3), (15.4, 16.3)])
        c = [w for w in out if w["w"] == "chat"][0]
        self.assertEqual((c["s"], c["e"]), (13.8, 14.2))
        self.assertTrue(c["retimed"])
        self.assertEqual((mv[0]["from"], mv[0]["to"]), (15.48, 13.8))
        self.assertEqual([w["w"] for w in out], ["so", "chat", "is"])

    def test_unsure_or_unmatched_or_small_or_huge_shift_untouched(self):
        words = [W("it's", 25.76, 26.0)]
        for h in ([W("it's", 24.8, 25.1, 0.3)], [W("its a", 24.8, 25.1)], [W("it's", 25.6, 25.9)],
                  [W("it's", 20.0, 20.3)]):
            out, mv = wr.retime(words, h, None)
            self.assertEqual(mv, [])
            self.assertEqual(out[0]["s"], 25.76)
            self.assertNotIn("retimed", out[0])

    def test_unsure_main_word_untouched(self):
        out, mv = wr.retime([W("chat", 15.5, 15.9, 0.3)], [W("chat", 13.8, 14.2)], None)
        self.assertEqual(mv, [])

    def test_heard_word_off_speech_is_refused(self):
        out, mv = wr.retime([W("chat", 15.5, 15.9)], [W("chat", 13.8, 14.2)], [(15.4, 16.0)])
        self.assertEqual(mv, [])

    def test_word_between_blocks_the_move(self):
        words = [W("hey", 14.5, 14.9), W("chat", 15.5, 15.9)]
        out, mv = wr.retime(words, [W("chat", 13.8, 14.2)], None)
        self.assertEqual(mv, [])
        self.assertEqual([(w["w"], w["s"]) for w in out], [("hey", 14.5), ("chat", 15.5)])

    def test_heard_word_used_once_and_order_kept(self):
        words = [W("go", 20.0, 20.3), W("on", 21.0, 21.2), W("go", 24.0, 24.3)]
        out, mv = wr.retime(words, [W("go", 19.0, 19.4)], None)
        self.assertEqual(len(mv), 1)
        self.assertEqual(mv[0]["to"], 19.0)
        self.assertTrue(all(w["e"] > w["s"] for w in out))
        self.assertTrue(all(out[i]["s"] <= out[i + 1]["s"] for i in range(len(out) - 1)))

    def test_end_never_runs_into_next_word(self):
        words = [W("a", 3.0, 3.2), W("chat", 6.0, 6.4)]
        out, mv = wr.retime(words, [W("chat", 4.0, 5.5)], None)
        self.assertEqual(len(mv), 1)
        self.assertLessEqual(out[1]["e"], 5.5)


if __name__ == "__main__":
    unittest.main()
