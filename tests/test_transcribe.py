import json, os, tempfile, unittest
from types import SimpleNamespace as NS
from clip_engine import transcribe as tr, captions as cap


def seg(s, e, text, words):
    return NS(start=s, end=e, text=text,
              words=[NS(word=w, start=a, end=b, probability=0.9) for w, a, b in words])


class FakeModel:
    def __init__(self):
        self.kw = None

    def transcribe(self, audio, **kw):
        self.kw = kw
        segs = [seg(0.0, 1.0, " no way", [(" no", 0.0, 0.4), (" way", 0.4, 1.0)]),
                seg(1.0, 2.0, " ", [(" ", 1.0, 1.1)])]
        return iter(segs), NS(language="en", language_probability=0.99)


class T(unittest.TestCase):
    def test_slice_window_and_cap(self):
        a = list(range(100))
        self.assertEqual(len(tr.slice_audio(a, 1, 3, sr=10)), 20)
        self.assertEqual(len(tr.slice_audio(a, 1, None, max_sec=2, sr=10)), 20)
        self.assertEqual(len(tr.slice_audio(a, 0, 999, sr=10)), 100)

    def test_collect_words_shape_feeds_captions(self):
        segs, info = tr.transcribe_audio(FakeModel(), [0.0], language="en")
        self.assertEqual(segs[0]["words"][0], {"w": "no", "s": 0.0, "e": 0.4, "p": 0.9})
        self.assertEqual(segs[1]["words"], [])  # blank word dropped
        self.assertIn("no", cap.to_ass(segs[0]["words"]))

    def test_vad_and_word_timestamps_are_requested(self):
        m = FakeModel()
        tr.transcribe_audio(m, [0.0], language="en", beam=1)
        self.assertTrue(m.kw["vad_filter"])
        self.assertTrue(m.kw["word_timestamps"])
        self.assertEqual(m.kw["beam_size"], 1)

    def test_vad_stats_seconds(self):
        spans, total = tr.vad_stats([0.0], get_ts=lambda a: [{"start": 16000, "end": 48000}])
        self.assertEqual(spans, [(1.0, 3.0)])
        self.assertAlmostEqual(total, 2.0)

    def test_dir_size_counts_a_symlinked_blob_once(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "blob"), "wb") as f:
                f.write(b"x" * 2_000_000)
            os.symlink(os.path.join(d, "blob"), os.path.join(d, "link"))
            self.assertAlmostEqual(tr.dir_size_mb(d), 2.0, places=1)  # one file, not two

    def test_peak_rss_positive(self):
        self.assertGreater(tr.peak_rss_mb(), 1.0)


if __name__ == "__main__":
    unittest.main()
