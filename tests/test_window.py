import json
import os
import tempfile
import unittest
from unittest import mock

from clip_engine import cli, window


class T(unittest.TestCase):
    def test_zero_zero_is_rejected_and_names_the_inputs(self):
        with self.assertRaises(ValueError) as c:
            window.check_window(0, 0)
        m = str(c.exception)
        self.assertIn("render_start", m)
        self.assertIn("render_end", m)
        self.assertIn("both are 0", m)

    def test_equal_and_reversed_rejected(self):
        for s, e in ((45, 45), (50, 40)):
            with self.assertRaises(ValueError):
                window.check_window(s, e)

    def test_too_short_negative_nan_and_text_rejected(self):
        for s, e in ((10, 11), (-1, 20), (float("nan"), 20), ("x", 20)):
            with self.assertRaises(ValueError):
                window.check_window(s, e)

    def test_good_window_passes_and_is_clamped_to_media(self):
        self.assertEqual(window.check_window(45, 85), (45.0, 85.0))
        self.assertEqual(window.check_window("45", "85", duration=300), (45.0, 85.0))
        s, e = window.check_window(45, 400, duration=300)
        self.assertAlmostEqual(e, 299.95)

    def test_start_past_media_rejected(self):
        with self.assertRaises(ValueError):
            window.check_window(299.8, 320, duration=300)
        with self.assertRaises(ValueError):
            window.check_window(100, 400, duration=102)

    def test_cli_cam_bench_zero_window_writes_error_json_and_fails(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("clip_engine.cam_bench.run") as run:
            rc = cli.main(["cam-bench", "x.mp4", "--start", "0", "--end", "0", "--out", d])
            self.assertEqual(rc, 1)
            run.assert_not_called()
            rep = json.load(open(os.path.join(d, "cam_bench.json")))
            self.assertIn("empty sample window", rep["error"])
            self.assertEqual(rep["inputs"]["end"], 0)
            self.assertTrue(os.path.exists(os.path.join(d, "cam_bench.md")))

    def test_cli_cam_bench_runtime_failure_also_writes_json(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("clip_engine.cam_bench.run", side_effect=RuntimeError("boom")):
            self.assertEqual(cli.main(["cam-bench", "x.mp4", "--start", "45", "--end", "85", "--out", d]), 1)
            self.assertIn("boom", json.load(open(os.path.join(d, "cam_bench.json")))["error"])

    def test_cli_render_zero_window_fails_before_render(self):
        with mock.patch("clip_engine.render_clip.render") as r:
            self.assertEqual(cli.main(["render", "x.mp4", "--start", "0", "--end", "0"]), 1)
        r.assert_not_called()


if __name__ == "__main__":
    unittest.main()
