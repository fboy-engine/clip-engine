import json
import os
import tempfile
import unittest

from clip_engine import stagewatch


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def jload(path):
    with open(path) as f:
        return json.load(f)


def make(tmp, clock, avail=None, **kw):
    box = {"avail": 8000.0}
    if avail is not None:
        box["avail"] = avail
    exits, logs = [], []
    w = stagewatch.Watch(tmp, clock=clock, avail_fn=lambda: box["avail"], rss_fn=lambda: 100.0, peak_fn=lambda: 120.0,
                         exit_fn=lambda c: exits.append(c), log=logs.append, **kw)
    return w, box, exits, logs


class StageWatchTest(unittest.TestCase):
    def test_stage_rows_are_flushed_after_every_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            clk = Clock()
            w, _, _, _ = make(tmp, clk)
            w.begin("asr", 480)
            clk.t = 42.0
            w.begin("track", 600)             # ends asr
            doc = jload(os.path.join(tmp, "render_stages.json"))
            self.assertEqual(doc["stages"][0]["stage"], "asr")
            self.assertEqual(doc["stages"][0]["wall_sec"], 42.0)
            self.assertEqual(doc["stages"][0]["peak_rss_mb"], 120.0)
            self.assertEqual(doc["current"], "track")
            self.assertTrue(jload(os.path.join(tmp, "render.json"))["partial"])

    def test_finished_render_json_is_never_overwritten_by_the_partial_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "render.json"), "w") as f:
                json.dump({"cut": {"frames": 5}}, f)
            clk = Clock()
            w, _, _, _ = make(tmp, clk)
            w.begin("x", 60)
            w.close()
            self.assertEqual(jload(os.path.join(tmp, "render.json")), {"cut": {"frames": 5}})
            self.assertEqual(jload(os.path.join(tmp, "render_stages.json"))["stages"][0]["stage"], "x")

    def test_stage_over_budget_stops_with_a_clear_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            clk = Clock()
            w, _, exits, logs = make(tmp, clk)
            w.begin("cam_prep", 360)
            clk.t = 359.0
            self.assertIsNone(w.check())
            clk.t = 361.0
            msg, code = w.check()
            self.assertIn("stage cam_prep timed out after 6 min", msg)
            self.assertEqual(code, stagewatch.EXIT_TIMEOUT)
            w.abort(msg, code)
            self.assertEqual(exits, [124])
            self.assertTrue(any("FAIL render: stage cam_prep timed out after 6 min" in l for l in logs))
            doc = jload(os.path.join(tmp, "render_stages.json"))
            self.assertIn("timed out", doc["aborted"])
            self.assertTrue(doc["stages"][-1]["status"].startswith("aborted"))

    def test_total_budget_fires_before_the_step_timeout(self):
        self.assertLess(stagewatch.TOTAL_BUDGET_SEC, 35 * 60)
        with tempfile.TemporaryDirectory() as tmp:
            clk = Clock()
            w, _, _, _ = make(tmp, clk, total_budget=100)
            w.begin("a", 1000)
            clk.t = 101.0
            msg, code = w.check()
            self.assertIn("render timed out after", msg)
            self.assertEqual(code, 124)

    def test_memory_guard_needs_consecutive_low_readings(self):
        with tempfile.TemporaryDirectory() as tmp:
            clk = Clock()
            w, box, exits, _ = make(tmp, clk, min_avail_mb=600, strikes=3)
            w.begin("captions_check", 600)
            box["avail"] = 300.0
            self.assertIsNone(w.check())
            self.assertIsNone(w.check())
            box["avail"] = 900.0              # a short dip that recovers is not a stop
            self.assertIsNone(w.check())
            box["avail"] = 300.0
            self.assertIsNone(w.check())
            self.assertIsNone(w.check())
            msg, code = w.check()
            self.assertIn("memory exhausted", msg)
            self.assertIn("captions_check", msg)
            self.assertEqual(code, stagewatch.EXIT_MEMORY)

    def test_lowest_free_memory_is_recorded_per_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            clk = Clock()
            w, box, _, _ = make(tmp, clk)
            w.begin("asr", 480)
            box["avail"] = 2500.0
            w.check()
            box["avail"] = 5000.0
            w.check()
            w.end()
            self.assertEqual(w.stages[0]["min_avail_mb"], 2500.0)

    def test_unreadable_memory_never_stops_the_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            clk = Clock()
            w = stagewatch.Watch(tmp, clock=clk, avail_fn=lambda: None, rss_fn=lambda: None, peak_fn=lambda: None,
                                 exit_fn=lambda c: None, log=lambda s: None)
            w.begin("a", 60)
            for _ in range(10):
                self.assertIsNone(w.check())

    def test_release_memory_runs(self):
        r = stagewatch.release_memory()
        self.assertTrue(r is None or r > 0)


if __name__ == "__main__":
    unittest.main()
