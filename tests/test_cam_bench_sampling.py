import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from clip_engine import cam_bench, cam_sr, cli, render_clip


class SampleCount(unittest.TestCase):
    def test_27_s_source(self):
        self.assertEqual(cam_bench.sample_count(27), 24)      # 27 s / 0.5 s = 54, capped at 24
        self.assertEqual(cam_bench.min_crops(27), 6)

    def test_19_s_source(self):
        self.assertEqual(cam_bench.sample_count(19), 24)      # 19 s / 0.5 s = 38, capped at 24
        self.assertEqual(cam_bench.min_crops(19), 6)

    def test_bounds_and_proportion(self):
        self.assertEqual(cam_bench.sample_count(3), 8)        # never fewer than 8
        self.assertEqual(cam_bench.sample_count(5), 10)       # 1 per 0.5 s in between
        self.assertEqual(cam_bench.sample_count(10), 20)
        self.assertEqual(cam_bench.sample_count(600), 24)     # never more than 24

    def test_full_minimum_stays_for_long_sources(self):
        self.assertEqual(cam_bench.min_crops(39.9), 6)
        self.assertEqual(cam_bench.min_crops(40), 8)
        self.assertEqual(cam_bench.min_crops(300), 8)


class CheckCrops(unittest.TestCase):
    def test_short_sources_need_6(self):
        for dur in (27, 19):
            self.assertEqual(cam_bench.check_crops(6, True, dur), 6)
            self.assertEqual(cam_bench.check_crops(24, True, dur), 6)
            with self.assertRaisesRegex(cam_bench.NotEnoughCrops, "only 5 real crops"):
                cam_bench.check_crops(5, True, dur)

    def test_long_sources_need_8(self):
        self.assertEqual(cam_bench.check_crops(8, True, 120), 8)
        with self.assertRaises(cam_bench.NotEnoughCrops):
            cam_bench.check_crops(7, True, 120)

    def test_missing_roi_counts_as_not_enough(self):
        with self.assertRaisesRegex(cam_bench.NotEnoughCrops, "ROI"):
            cam_bench.check_crops(24, False, 27)


class SkippedBenchDoesNotBlock(unittest.TestCase):
    def args(self, out):
        return argparse.Namespace(media="x.mp4", start=2.0, end=29.0, out=out, vod_id="", url="", formats=False,
                                  cache_dir="", candidates="")

    def test_cli_skips_with_a_warning_and_returns_0(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(cam_bench, "run", side_effect=cam_bench.NotEnoughCrops("only 4 real crops could be cut")):
                with contextlib.redirect_stdout(io.StringIO()) as so:
                    rc = cli.cmd_cam_bench(self.args(d))
            self.assertEqual(rc, 0)
            self.assertIn("WARNING cam-bench skipped", so.getvalue())
            with open(os.path.join(d, "cam_bench.json")) as f:
                j = json.load(f)
            self.assertTrue(j["skipped"])
            self.assertNotIn("error", j)

    def test_cli_still_fails_on_a_real_error(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(cam_bench, "run", side_effect=ValueError("boom")):
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = cli.cmd_cam_bench(self.args(d))
            self.assertEqual(rc, 1)

    def test_render_takes_the_default_chain_when_the_bench_was_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "cam_bench.json"), "w") as f:
                json.dump({"skipped": True, "warning": "only 4 real crops"}, f)
            b3 = render_clip.load_b3(d)
        self.assertTrue(b3["bench_skipped"])
        self.assertTrue(b3["ok"])
        self.assertEqual(b3["method"], "realesrgan")
        self.assertEqual(b3["blend"], cam_sr.BLEND_START)
        self.assertEqual(b3["deblock_vf"], "")
        self.assertIn("only 4 real crops", b3["reason"])


if __name__ == "__main__":
    unittest.main()
