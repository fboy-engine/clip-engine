import unittest

import numpy as np

from clip_engine import analyze as an, rank_local as rl


def flat_db(n, base=-40.0):
    return [base] * n


class T(unittest.TestCase):
    def test_rms_db_per_second(self):
        sr = 16000
        a = np.zeros(sr * 3, dtype=np.float32)
        a[sr:2 * sr] = 0.5
        db = an.rms_db(a, sr)
        self.assertEqual(len(db), 3)
        self.assertGreater(db[1], -8)
        self.assertLess(db[0], -100)

    def test_speech_per_sec(self):
        sp = an.speech_per_sec([(0.5, 2.5)], 4)
        self.assertEqual([round(x, 2) for x in sp], [0.5, 1.0, 0.5, 0.0])

    def test_motion_series_burst(self):
        rng = np.random.default_rng(1)
        fr = np.zeros((40, 9, 16), dtype=np.uint8)
        fr[20:24] = rng.integers(0, 255, (4, 9, 16))
        m = an.motion_series(fr, fps=4)
        self.assertEqual(len(m), 10)
        self.assertGreater(m[5], 5 * max(m[1], 1e-6))

    def test_quiet_run_and_cut_points(self):
        db = [-20] * 10 + [-50] * 3 + [-20] * 10
        self.assertEqual(an.quiet_runs(db), [(10, 13)])
        pts = an.cut_points([(0, 4), (5, 8)], db)
        self.assertIn(4.5, pts)
        self.assertIn(11.5, pts)
        self.assertEqual(an.snap(10.2, pts, 3), 11.5)
        self.assertEqual(an.snap(30, pts, 3), 30)

    def test_flat_input_gives_no_candidates(self):
        n = 200
        self.assertEqual(an.build_windows(flat_db(n), [0.0] * n, [0.1] * n, []), [])

    def test_quiet_action_stretch_is_a_candidate_and_passes_action_gate(self):
        n = 200
        db = flat_db(n, -35.0)
        db[100] = db[101] = -15.0           # crash sound
        motion = [0.02] * n
        for i in range(98, 104):
            motion[i] = 0.30                # screen shakes
        wins = an.build_windows(db, [0.0] * n, motion, [])   # nobody talks
        self.assertEqual(len(wins), 1)
        w = wins[0]
        self.assertTrue(config_len_ok(w))
        self.assertTrue(w.start <= 100 <= w.end)
        self.assertAlmostEqual(w.peak_t, 100, delta=3)
        self.assertLess(w.speech_ratio, 0.01)
        self.assertTrue(rl.payoff_gate(w)[0])
        self.assertIn("mostly no voice (action stretch)", an.reasons(w))

    def test_voice_only_moment_still_works(self):
        n = 200
        db = flat_db(n, -35.0)
        db[60] = db[61] = -12.0
        speech = [0.0] * n
        for i in range(50, 70):
            speech[i] = 1.0
        wins = an.build_windows(db, speech, [], [(50, 70)])
        self.assertTrue(wins)
        w = wins[0]
        w.text = "no way he actually did it"
        self.assertTrue(rl.payoff_gate(w)[0])

    def test_nearby_peaks_are_deduped(self):
        n = 200
        db = flat_db(n, -35.0)
        db[100] = db[101] = db[112] = db[113] = -15.0
        wins = an.build_windows(db, [0.0] * n, [], [])
        self.assertEqual(len(wins), 1)

    def test_idle_motion_without_audio_lift_fails_gate(self):
        w = rl.Window(0, 35, "", 0.0, -30, -33, motion_lift=3.0)
        self.assertFalse(rl.payoff_gate(w)[0])

    def test_short_media_has_no_windows(self):
        self.assertEqual(an.build_windows([-30.0] * 10, [0.0] * 10, [], []), [])


def config_len_ok(w):
    from clip_engine.config import MIN_LEN, MAX_LEN
    return MIN_LEN <= w.end - w.start <= MAX_LEN


if __name__ == "__main__":
    unittest.main()
