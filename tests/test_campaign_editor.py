"""Engine -> panel wiring with a fake GitHub. No network."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone

from campaign_lib import config, editor, states
from campaign_lib.ghclient import GitHubError
from campaign_lib.states import Item

CFG = {**config.load(), "pause": False}     # the real file has PAUSE on; these tests are about the running loop
NOW = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)
ISO = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_zip(grade=None, render=None, mp4=True, cam=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        if mp4:
            z.writestr("clip.mp4", b"mp4bytes")
        if grade is not None:
            z.writestr("grade.json", json.dumps(grade))
        if render is not None:
            z.writestr("render.json", json.dumps(render))
        if cam is not None:
            z.writestr("cam_find.json", json.dumps(cam))
    return zipfile.ZipFile(io.BytesIO(b.getvalue()))


GOOD = {"checks": [{"name": "video format", "status": "pass", "detail": ""}, {"name": "x", "status": "warn", "detail": "w"},
                   {"name": "cam", "status": "n/a", "detail": ""}]}
BAD = {"checks": [{"name": "captions 90%", "status": "fail", "detail": "coverage 89.2%"}]}


class FakeGH:
    def __init__(self):
        self.dispatched, self.runs, self.run_info, self.zips, self.steps = [], [], {}, {}, {}
        self.dispatch_error = None

    def dispatch(self, workflow, inputs, ref="main"):
        if self.dispatch_error:
            raise self.dispatch_error
        self.dispatched.append((workflow, inputs))

    def list_runs(self, workflow, since):
        return self.runs

    def get_run(self, rid):
        return self.run_info[str(rid)]

    def failed_step(self, rid):
        return self.steps.get(str(rid), "")

    def artifact_zip(self, rid, prefix):
        return self.zips.get(str(rid))


def probe(url):
    return {"duration": 37.7}


def items(*ns):
    return [Item(n=n, source="https://m.twitch.tv/laurenhamdenn/clip/C%d" % n) for n in ns]


def run(rid, actor="github-actions[bot]", created=NOW):
    return {"id": rid, "created_at": ISO(created), "triggering_actor": {"login": actor}}


class Window(unittest.TestCase):
    def test_window(self):
        self.assertEqual(editor.clip_window(37.7), (2.0, 29.0))
        self.assertEqual(editor.clip_window(19), (2.0, 19.0))
        self.assertEqual(editor.clip_window(11), (0.0, 11.0))
        with self.assertRaises(ValueError):
            editor.clip_window(5)


class Dispatch(unittest.TestCase):
    def test_next_queued_link_is_dispatched_with_the_safe_inputs(self):
        gh, its, log = FakeGH(), items(1, 2), []
        editor.Editor(gh, CFG, probe).advance(its, NOW, log)
        wf, inp = gh.dispatched[0]
        self.assertEqual(wf, "clip.yml")
        self.assertEqual((inp["render"], inp["dry_run"], inp["warp"], inp["cookies"]), ("true", "true", "false", "false"))
        self.assertEqual((inp["render_start"], inp["render_end"]), ("2", "29"))
        self.assertNotIn("cam_rect", inp)
        self.assertEqual(its[0].state, "EDITING")
        self.assertTrue(its[0].run.startswith("PENDING@"))
        self.assertEqual(its[1].state, "QUEUED")  # one clip at a time

    def test_cam_rect_hint_comes_from_campaign_json(self):
        it = Item(n=8, source=[s for s in CFG["sources"] if s["n"] == 8][0]["url"])
        gh = FakeGH()
        editor.Editor(gh, CFG, probe).advance([it], NOW, [])
        self.assertEqual(gh.dispatched[0][1]["cam_rect"], "0.679,0,1,0.393")

    def test_dispatch_failure_goes_back_to_queued_and_is_logged(self):
        gh, its, log = FakeGH(), items(1), []
        gh.dispatch_error = GitHubError("HTTP 500")
        editor.Editor(gh, CFG, probe).advance(its, NOW, log)
        self.assertEqual(its[0].state, "QUEUED")
        self.assertEqual(log[-1][0], "EDITOR_ERROR")

    def test_three_failed_probes_skip_the_clip_and_the_queue_moves_on(self):
        def bad(url):
            raise OSError("gone")
        its, gh = items(1, 2), FakeGH()
        ed = editor.Editor(gh, CFG, bad)
        for _ in range(3):
            ed.advance(its, NOW, [])
        self.assertEqual(its[0].state, "SKIPPED")
        self.assertIs(states.head(its), its[1])


class FindRun(unittest.TestCase):
    def start(self):
        gh, its = FakeGH(), items(1, 2)
        ed = editor.Editor(gh, CFG, probe)
        ed.advance(its, NOW, [])
        return gh, its, ed

    def test_run_id_is_recorded_and_other_clips_runs_are_excluded(self):
        gh, its, ed = self.start()
        its[1].run = "111"  # another clip already owns run 111
        gh.runs = [run(111), run(222, actor="someone"), run(333)]
        ed.advance(its, NOW + timedelta(seconds=40), [])
        self.assertEqual(its[0].run, "333")  # our bot's run first, 111 excluded

    def test_lost_dispatch_is_retried_then_skipped(self):
        gh, its, ed = self.start()
        for k in range(1, 4):
            ed.advance(its, NOW + timedelta(seconds=700 * k * 2), [])      # not found -> QUEUED
            if its[0].state == "SKIPPED":
                break
            ed.advance(its, NOW + timedelta(seconds=700 * k * 2 + 1), [])  # dispatched again
        self.assertEqual(its[0].state, "SKIPPED")
        self.assertIn("never appeared", its[0].note)
        mine = [d for d in gh.dispatched if d[1]["url"] == its[0].source]
        self.assertEqual(len(mine), 3)
        self.assertEqual(its[1].state, "EDITING")  # and the queue moved on


class Finish(unittest.TestCase):
    def running(self, grade, render=None, conclusion="success", attempt=editor.MAX_FIX_TRIES, cfg=None, **zk):
        gh, its = FakeGH(), items(1, 2)
        ed = editor.Editor(gh, cfg or CFG, probe)
        ed.advance(its, NOW, [])
        its[0].note = "attempt %d" % attempt     # default: no retries left, so a failure is a skip
        its[0].run = "900"
        gh.run_info["900"] = {"status": "completed", "conclusion": conclusion, "created_at": ISO(NOW)}
        gh.zips["900"] = make_zip(grade, render if render is not None else {"hook": {"text": "WAIT WHAT IS THAT"}}, **zk)
        return gh, its, ed

    def test_in_progress_changes_nothing(self):
        gh, its, ed = self.running(GOOD)
        gh.run_info["900"] = {"status": "in_progress", "created_at": ISO(NOW)}
        ed.advance(its, NOW + timedelta(minutes=5), [])
        self.assertEqual(its[0].state, "EDITING")
        self.assertEqual(its[1].state, "QUEUED")

    def test_passing_run_becomes_ready_with_the_exact_reach_caption(self):
        gh, its, ed = self.running(GOOD)
        log = []
        ed.advance(its, NOW + timedelta(minutes=30), log)
        self.assertEqual(its[0].state, "READY")
        cap = its[0].caption
        self.assertTrue(cap.startswith("Wait what is that"))
        self.assertIn("#LaurenHamden", cap)
        self.assertIn("@LaurenHamden", cap)
        self.assertEqual(its[0].run, "900")           # kept: the uploader fetches the mp4 from this run
        self.assertEqual(its[1].state, "EDITING")      # next clip started in the same pass
        self.assertIn(("READY", "clip #1 passed every gate"), log)

    def test_failed_gate_skips_with_the_reason_and_the_queue_moves_on(self):
        gh, its, ed = self.running(BAD)
        ed.advance(its, NOW + timedelta(minutes=30), [])
        self.assertEqual(its[0].state, "SKIPPED")
        self.assertIn("captions 90%", its[0].note)
        self.assertEqual(its[1].state, "EDITING")
        self.assertIs(states.head(its), its[1])

    def test_failed_run_skips_with_step_and_cam_reason(self):
        gh, its, ed = self.running(None, conclusion="failure", cam={"reasons": ["no straight border"]})
        gh.steps["900"] = "Render clip"
        ed.advance(its, NOW + timedelta(minutes=30), [])
        self.assertEqual(its[0].state, "SKIPPED")
        self.assertIn("Render clip", its[0].note)
        self.assertIn("no straight border", its[0].note)

    def test_no_clip_or_no_grade_never_passes(self):
        for kw in ({"mp4": False}, {}):
            gh, its, ed = self.running(GOOD if kw else None, **kw)
            ed.advance(its, NOW + timedelta(minutes=30), [])
            self.assertEqual(its[0].state, "SKIPPED", kw)

    def test_unusable_hook_falls_back_to_a_safe_line(self):
        gh, its, ed = self.running(GOOD, render={"hook": {"text": "visit http://x.y #fyp"}})
        ed.advance(its, NOW + timedelta(minutes=30), [])
        self.assertEqual(its[0].state, "READY")
        self.assertTrue(its[0].caption.startswith(editor.FALLBACK_LINE))

    def test_run_that_never_finishes_is_skipped(self):
        gh, its, ed = self.running(GOOD)
        gh.run_info["900"] = {"status": "in_progress", "created_at": ISO(NOW)}
        ed.advance(its, NOW + timedelta(seconds=editor.MAX_WAIT_S + 60), [])
        self.assertEqual(its[0].state, "SKIPPED")


class NeedsFix(Finish):
    def test_first_failure_is_recut_at_once_with_a_different_window(self):
        gh, its, ed = self.running(BAD, attempt=1)
        log = []
        ed.advance(its, NOW + timedelta(minutes=30), log)
        self.assertIn("NEEDS_FIX", [e[0] for e in log])       # parked with the reason ...
        self.assertEqual(its[0].state, "EDITING")             # ... and re-cut in the same pass (head of the queue)
        self.assertTrue(its[0].note.startswith("attempt 2"))
        self.assertEqual(its[1].state, "QUEUED")
        old, new = gh.dispatched[0][1], gh.dispatched[-1][1]
        self.assertEqual(old["url"], new["url"])
        self.assertNotEqual(old["render_start"], new["render_start"])

    def test_failure_reason_is_kept_in_the_log_until_the_retry_overwrites_it(self):
        gh, its, ed = self.running(BAD, attempt=1)
        log = []
        ed.advance(its, NOW + timedelta(minutes=30), log)
        why = [e[1] for e in log if e[0] == "NEEDS_FIX"][0]
        self.assertIn("captions 90%", why)

    def test_skipped_only_after_the_last_try(self):
        gh, its, ed = self.running(BAD, attempt=editor.MAX_FIX_TRIES - 1)
        ed.advance(its, NOW + timedelta(minutes=30), [])
        self.assertNotEqual(its[0].state, "SKIPPED")
        gh2, its2, ed2 = self.running(BAD, attempt=editor.MAX_FIX_TRIES)
        ed2.advance(its2, NOW + timedelta(minutes=30), [])
        self.assertEqual(its2[0].state, "SKIPPED")

    def test_window_shift_keeps_25_seconds(self):
        self.assertEqual(editor.clip_window(37.7, None, shift=4.0), (6.0, 33.0))
        self.assertEqual(editor.clip_window(37.7, None, shift=12.0), (2.0, 29.0))     # would leave < 25 s: no shift
        self.assertEqual(editor.clip_window(37.7), (2.0, 29.0))


class Pause(unittest.TestCase):
    PCFG = {**CFG, "pause": True}

    def test_campaign_json_ships_paused(self):
        self.assertTrue(config.load().get("pause"))

    def test_paused_loop_renders_nothing_and_skips_nothing(self):
        gh, its = FakeGH(), items(1, 2)
        editor.Editor(gh, self.PCFG, probe).advance(its, NOW, [])
        self.assertEqual(gh.dispatched, [])
        self.assertEqual([i.state for i in its], ["QUEUED", "QUEUED"])

    def test_paused_failure_is_parked_never_consumed(self):
        gh, its = FakeGH(), items(1, 2)
        ed = editor.Editor(gh, CFG, probe)
        ed.advance(its, NOW, [])
        its[0].run, its[0].note = "900", "attempt 3"
        gh.run_info["900"] = {"status": "completed", "conclusion": "success", "created_at": ISO(NOW)}
        gh.zips["900"] = make_zip(BAD, {})
        paused = editor.Editor(gh, self.PCFG, probe)
        paused.advance(its, NOW + timedelta(minutes=30), [])
        self.assertEqual(its[0].state, "QUEUED")            # attempt 3 would skip when not paused
        self.assertIn("NEEDS_FIX (paused)", its[0].note)


class Fetch(unittest.TestCase):
    def test_fetch_clip_writes_the_mp4_and_sets_file(self):
        gh = FakeGH()
        gh.zips["900"] = make_zip(GOOD, {})
        it = Item(n=1, source="s", run="900")
        with tempfile.TemporaryDirectory() as d:
            p = editor.fetch_clip(gh, it, d)
            with open(p, "rb") as f:
                self.assertEqual(f.read(), b"mp4bytes")
            self.assertEqual(it.file, p)

    def test_fetch_needs_a_finished_run(self):
        with self.assertRaises(GitHubError):
            editor.fetch_clip(FakeGH(), Item(n=1, source="s", run="PENDING@x"), "/tmp/x")


if __name__ == "__main__":
    unittest.main()
