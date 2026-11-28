"""Batch planning and result reading (pure, stdlib) plus text checks of the workflows (CI has no PyYAML)."""
import json
import os
import tempfile
import unittest
from unittest import mock

from clip_engine import batch, findrun

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


class Plan(unittest.TestCase):
    def setUp(self):
        self.finds = batch.finds(json.loads(read(*batch.CAMPAIGN.split("/"))))

    def test_every_section_and_every_clip_is_searched(self):
        self.assertEqual([f["tag"] for f in self.finds],
                         ["s0", "s600", "s1200", "s1800", "s2100", "s2400", "s3000", "s3172",
                          "c1", "c3", "c4", "c5", "c6", "c7", "c8", "c9"])
        self.assertEqual(self.finds[-2]["cam_rect"], "0.679,0,1,0.393")

    def test_window_is_padded_wide_enough_and_stays_in_the_file(self):
        self.assertEqual(batch.window(100, 135, 600), (86, 149))
        self.assertEqual(batch.window(0, 25, 600), (0, 54))
        self.assertEqual(batch.window(575, 600, 600), (546, 600))
        self.assertEqual(batch.window(0, 30, 37.66), (0, 37.6))

    def test_jobs(self):
        cands = [{"start": 100, "end": 135}] * 7
        jobs, skipped = batch.renders(self.finds, lambda f: (None, None) if f["tag"] == "c6" else
                                      ([], 20) if f["tag"] == "c7" else (cands, 600))
        self.assertEqual(jobs[0], batch.REGRESSION)
        self.assertEqual(len(jobs), 1 + 8 * 5 + 6 * 2)
        self.assertEqual(len({j["tag"] for j in jobs}), len(jobs))
        self.assertTrue(all(j["dense_cut"] for j in jobs if j["tag"].startswith("s")))
        self.assertEqual([(s["tag"], s["result"]) for s in skipped], [("c6", "CRASH"), ("c7", "SKIP")])
        self.assertLessEqual(len(jobs), 256)


class Verdicts(unittest.TestCase):
    def test_each_kind(self):
        c = batch.classify
        r = c("x\nFAIL render: ValueError: tracking gate failed: 69.4%\n", None, "failure", [])
        self.assertEqual(r[:2], ("FAIL", "tracking"))
        self.assertEqual(c("FAIL render: stage asr timed out after 8 min", None, "failure", [])[0], "TIMEOUT")
        r = c("FAIL  captions style: 2 highlights\nGRADE: FAIL (1 failed, 0 warnings, 9 passed)", None, "failure", [])
        self.assertEqual(r[:2], ("FAIL", "grade: captions style"))
        self.assertEqual(c("GRADE: PASS (0 failed)", None, "success", [])[0], "PASS")
        self.assertEqual(c("GRADE: PASS (0 failed)", None, "failure", [])[0], "CRASH")
        self.assertEqual(c(None, "FAIL cam-bench: no confident cam", "failure", [])[:2], ("FAIL", "cam benchmark"))
        self.assertEqual(c(None, None, "failure", ["The job has exceeded the maximum execution time"])[0], "TIMEOUT")
        self.assertEqual(c(None, None, None, [])[0], "CRASH")

    def test_report_names_the_regression_and_counts_gates(self):
        rows = batch.summarize([batch.REGRESSION], [], "/nonexistent", "1",
                               {"regression": ("failure", ["Process completed with exit code 1."])})
        md = batch.markdown(rows, "u")
        self.assertIn("Regression window (must pass): **CRASH**", md)
        self.assertIn("- crash: 1", md)

    def test_job_names_from_the_api(self):
        def api(path):
            if "annotations" in path:
                return [{"annotation_level": "failure", "message": "boom"}]
            return {"jobs": [{"id": 1, "name": "render s0-w1 / clip", "conclusion": "failure"},
                             {"id": 2, "name": "render s0-w1 / scan", "conclusion": "skipped"}]}
        self.assertEqual(batch.run_jobs("1", api), {"s0-w1": ("failure", ["boom"])})


class FindWithoutWindows(unittest.TestCase):
    def test_an_empty_candidate_file_is_written(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(findrun.transcribe, "decode_pcm", return_value=[]), \
                mock.patch.object(findrun.transcribe, "vad_stats", return_value=([], 0.0)), \
                mock.patch.object(findrun.analyze, "decode_motion", return_value=[]):
            self.assertEqual(findrun.run("x.mp4", 0, "small", 5, d, d), 1)
            with open(os.path.join(d, "candidates.json")) as f:
                self.assertEqual(json.load(f)["candidates"], [])


class Workflows(unittest.TestCase):
    def test_batch_is_dry_run_only_and_calls_clip(self):
        t = read(".github", "workflows", "batch.yml")
        self.assertIn('options: ["true"]', t)
        self.assertNotIn("dry_run: false", t)
        self.assertEqual(t.count("uses: ./.github/workflows/clip.yml"), 2)
        self.assertEqual(t.count("fail-fast: false"), 2)

    def test_clip_is_callable_and_matrix_jobs_do_not_collide(self):
        t = read(".github", "workflows", "clip.yml")
        self.assertIn("workflow_call:", t)
        self.assertIn("continue-on-error: ${{ !!inputs.tag }}", t)
        dispatch = t.split("  workflow_dispatch:", 1)[1].split("  workflow_call:", 1)[0]
        self.assertNotIn("\n      tag:", dispatch)          # GitHub allows 25 dispatch inputs and they are all used
        for kind in ("clip", "dl-check", "find"):
            self.assertIn("name: %s-${{ github.run_id }}${{ inputs.tag" % kind, t)


if __name__ == "__main__":
    unittest.main()


class Rounds(unittest.TestCase):
    def test_later_round_skips_done_and_regression(self):
        f = [{"tag": "s0", "kind": "stream", "url": "u", "start_sec": "0", "end_sec": "600", "cam_rect": ""}]
        cands = [{"start": 100, "end": 120}, {"start": 300, "end": 320}, {"start": 305, "end": 325}]
        jobs, _ = batch.renders(f, lambda j: (cands, 600), done=[(90, 135)], rnd=1)
        tags = [j["tag"] for j in jobs]
        self.assertNotIn("regression", tags)
        self.assertEqual(len(jobs), 1)                   # first is done, third overlaps the second
        self.assertTrue(tags[0].startswith("s0-r1"))
