import unittest

import numpy as np

from clip_engine import balance


class Balance(unittest.TestCase):
    def test_gunfire_over_speech_shows_a_gap(self):
        sr = 16000
        t = np.arange(sr * 4) / sr
        x = 0.03 * np.sin(2 * np.pi * 200 * t)
        x[sr * 3:sr * 3 + 4000] += 0.5 * np.sin(2 * np.pi * 90 * t[:4000])
        b = balance.balance(x, sr, [[0.2, 2.8]])
        self.assertGreater(b["gap_db"], 10)
        self.assertLess(b["voice_db"], b["loud_db"])

    def test_no_speech_is_reported_not_faked(self):
        b = balance.balance(np.zeros(16000), 16000, [])
        self.assertIsNone(b["gap_db"])


if __name__ == "__main__":
    unittest.main()
