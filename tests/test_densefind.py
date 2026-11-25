import unittest

from clip_engine import densefind


class DenseFind(unittest.TestCase):
    def test_finds_the_continuous_talk(self):
        sp = [0.2] * 40 + [1.0] * 40 + [0.3] * 40
        w = densefind.dense_windows(sp, offset=300)
        self.assertTrue(w)
        self.assertGreaterEqual(w[0]["file_start"], 40)
        self.assertLessEqual(w[0]["file_end"], 80)
        self.assertEqual(w[0]["stream_start"], 300 + w[0]["file_start"])
        self.assertGreaterEqual(w[0]["speech_pct"], 85)

    def test_sparse_speech_gives_nothing(self):
        self.assertEqual(densefind.dense_windows([0.6] * 200), [])

    def test_long_pause_is_rejected(self):
        sp = [1.0] * 14 + [0.0] * 4 + [1.0] * 14
        self.assertEqual(densefind.dense_windows(sp, min_pct=0.7), [])

    def test_windows_do_not_overlap(self):
        w = densefind.dense_windows([1.0] * 200, top=5)
        for a, b in zip(w, w[1:]):
            self.assertGreaterEqual(b["file_start"], a["file_end"])

    def test_short_input(self):
        self.assertEqual(densefind.dense_windows([1.0] * 10), [])


if __name__ == "__main__":
    unittest.main()
