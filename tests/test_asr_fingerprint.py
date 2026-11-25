"""ASR fingerprint: the same transcript always hashes the same, a changed word or time changes it."""
import unittest

from clip_engine import render_clip as rc

WORDS = [{"w": "no", "s": 0.1, "e": 0.3}, {"w": "way", "s": 0.3, "e": 0.62}]


class Fingerprint(unittest.TestCase):
    def test_same_words_same_hash(self):
        self.assertEqual(rc.words_fingerprint(WORDS), rc.words_fingerprint([dict(w) for w in WORDS]))

    def test_changed_word_or_time_changes_it(self):
        base = rc.words_fingerprint(WORDS)
        self.assertNotEqual(base, rc.words_fingerprint([{**WORDS[0], "w": "yes"}, WORDS[1]]))
        self.assertNotEqual(base, rc.words_fingerprint([WORDS[0], {**WORDS[1], "e": 0.7}]))

    def test_cpu_model_is_text(self):
        self.assertIsInstance(rc.cpu_model(), str)
        self.assertTrue(rc.cpu_model())


if __name__ == "__main__":
    unittest.main()
