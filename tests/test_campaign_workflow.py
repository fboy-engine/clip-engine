"""The campaign workflow file really uses the loop driver (text checks, stdlib only: CI has no PyYAML), the
watchdog only starts a loop when none is alive, and the loop run never renders or uploads."""
import os
import re
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from campaign_lib import cli, engine, loop, service, sheet
from tests.test_campaign_loop import Clock, GH
from tests.test_campaign_poster import CFG, MemIO, fake_item

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github", "workflows", "campaign-lauren-hamden.yml")
T0 = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)
CFG2 = dict(CFG, secrets={"instagram_token": "IG_ACCESS_TOKEN_LAUREN", "instagram_user_id": "IG_USER_ID_LAUREN",
                          "composio": "COMPOSIO_API_KEY", "gemini": "GEMINI_API_KEY"})


def text():
    with open(WF, encoding="utf-8") as f:
        return f.read()


def job(name):
    """The text of one top-level job (from '  name:' to the next top-level job)."""
    m = re.search(r"^  %s:\n(.*?)(?=^  [a-z]+:\n|\Z)" % name, text().split("\njobs:\n", 1)[1], re.S | re.M)
    assert m, "job %s not found" % name
    return m.group(1)


class WorkflowUsesTheLoop(unittest.TestCase):
    def test_loop_and_watchdog_modes_exist_with_slot_and_token_inputs(self):
        t = text()
        for needle in ("- loop", "- watchdog", "slot:", "handoff_token:"):
            self.assertIn(needle, t)
        self.assertIn("campaign_lib.cli loop", job("loop"))
        self.assertIn("campaign_lib.cli watchdog", job("watchdog"))

    def test_the_loop_handoff_dispatches_exactly_this_workflow_with_these_inputs(self):
        self.assertEqual(loop.LOOP_WORKFLOW, "campaign-lauren-hamden.yml")
        self.assertTrue(os.path.basename(WF) == loop.LOOP_WORKFLOW)
        inputs = text().split("inputs:", 1)[1].split("\npermissions:", 1)[0]
        for k in ("mode:", "slot:", "handoff_token:"):  # the keys do_handoff() sends
            self.assertIn("      " + k, inputs)

    def test_each_slot_has_its_own_group_and_nothing_is_cancelled(self):
        self.assertIn("group: campaign-lauren-hamden-loop-${{ inputs.slot }}", job("loop"))
        self.assertNotIn("cancel-in-progress: true", text())
        self.assertEqual(text().count("cancel-in-progress: false"), 4)  # cli, loop, watchdog, linkcheck
        # other jobs must never queue behind a 5 h loop
        for name in ("cli", "watchdog", "linkcheck"):
            self.assertNotIn("campaign-lauren-hamden-loop", job(name))

    def test_the_loop_is_dispatchable_into_the_other_slot_while_this_one_runs(self):
        self.assertEqual((loop.next_slot("a"), loop.next_slot("b"), loop.next_slot("w")), ("b", "a", "a"))

    def test_loop_timeout_is_just_above_the_hard_stop_and_below_6h(self):
        mins = int(re.search(r"timeout-minutes:\s*(\d+)", job("loop")).group(1))
        self.assertGreater(mins * 60, engine.HARD_STOP_S)
        self.assertLessEqual(mins, 359)

    def test_cron_is_a_watchdog_only(self):
        t = text()
        self.assertEqual(len(re.findall(r"^\s*- cron:", t, re.M)), 1)
        for name in ("cli", "loop", "linkcheck"):
            self.assertNotIn("event_name == 'schedule'", job(name))
            self.assertIn("event_name == 'workflow_dispatch'", job(name))
        self.assertIn("event_name == 'schedule'", job("watchdog"))
        self.assertNotIn("tick", job("watchdog").replace("watchdog", ""))

    def test_the_loop_can_dispatch_and_never_renders(self):
        self.assertIn("actions: write", text())
        body = job("loop")
        for banned in ("ffmpeg", "opencv", "clip_engine", "onnx", "cam_bench", "render"):
            self.assertNotIn(banned, body.replace("Never renders", ""), banned)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", body)

    def test_no_secret_is_echoed_and_no_instagram_call_is_in_the_workflow(self):
        t = text()
        self.assertNotRegex(t, r"echo[^\n]*secrets\.")
        self.assertNotIn("graph.facebook.com", t)


class CliKnowsTheModes(unittest.TestCase):
    def test_parser_accepts_loop_and_watchdog(self):
        for argv in (["loop", "--slot", "a", "--handoff-token", "t", "--fake"], ["watchdog"]):
            with mock.patch.object(cli, "cmd_loop", return_value=0), mock.patch.object(cli, "cmd_watchdog", return_value=0):
                self.assertEqual(cli.main(argv), 0)


class Watchdog(unittest.TestCase):
    def run_wd(self, cells, fail=False):
        gh, logs = GH(), []
        if fail:
            gh.dispatch = mock.Mock(side_effect=RuntimeError("HTTP 500"))
        code = service.run_watchdog(CFG2, MemIO([fake_item()], cells), gh, T0, lambda ev, **kw: logs.append((ev, kw)))
        return code, gh, logs

    def test_dead_loop_is_restarted_in_slot_w_with_no_token(self):
        code, gh, _ = self.run_wd({"lock_run": "A", "heartbeat": sheet.iso(T0 - timedelta(minutes=10))})
        self.assertEqual(code, 0)
        self.assertEqual(gh.calls, [("campaign-lauren-hamden.yml", {"mode": "loop", "slot": "w", "handoff_token": ""})])

    def test_never_started_is_started(self):
        self.assertEqual(len(self.run_wd({})[1].calls), 1)

    def test_live_loop_is_left_alone(self):
        _, gh, _ = self.run_wd({"lock_run": "A", "heartbeat": sheet.iso(T0 - timedelta(seconds=40))})
        self.assertEqual(gh.calls, [])

    def test_stop_is_respected(self):
        _, gh, _ = self.run_wd({"lock_run": "STOP", "heartbeat": ""})
        self.assertEqual(gh.calls, [])

    def test_failed_dispatch_is_logged_and_does_not_crash(self):
        code, _, logs = self.run_wd({}, fail=True)
        self.assertEqual(code, 0)
        self.assertEqual(logs[-1][1]["action"], "dispatch failed")


class LoopRun(unittest.TestCase):
    def test_a_fake_loop_runs_to_its_5h30_handoff_with_no_network_and_never_renders(self):
        io, clock, gh, logs = MemIO([fake_item(state="QUEUED")]), Clock(), GH(), []
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("no process may start")):
            code = service.run_loop(CFG2, io, gh, "a", "", fake=True, now_fn=clock, sleep_fn=clock.sleep,
                                    log=lambda ev, **kw: logs.append(ev), environ={"GITHUB_RUN_ID": "9", "GITHUB_RUN_ATTEMPT": "1"})
        self.assertEqual(code, 0)
        self.assertEqual(logs[0], "LOOP_START")
        self.assertEqual(logs[-1], "LOOP_HARD_STOP")
        self.assertEqual(io.cells["lock_run"], "9-1")  # handed over, never taken by anyone: the lock stays offered
        self.assertEqual([c[1]["slot"] for c in gh.calls], ["b"])
        self.assertIn("TICK", logs)

    def test_quiet_ticks_print_nothing(self):
        io, clock, logs = MemIO([fake_item(state="QUEUED")]), Clock(), []
        service.run_loop(CFG2, io, GH(), "a", "", fake=True, now_fn=clock, sleep_fn=clock.sleep,
                         log=lambda ev, **kw: logs.append(ev), environ={})
        self.assertLess(logs.count("TICK"), 20)  # hundreds of ticks, only changes are printed


class Probe(unittest.TestCase):
    def fake_run(self, rc=0, out="{}"):
        return mock.Mock(return_value=subprocess.CompletedProcess([], rc, stdout=out, stderr=""))

    def test_duration_is_returned(self):
        with mock.patch.object(subprocess, "run", self.fake_run(0, '{"duration": 31.5}')):
            self.assertEqual(service.probe_source("u"), {"duration": 31.5})

    def test_unusable_sources_are_valueerror_and_network_trouble_is_not(self):
        for out in ('{"is_live": true, "duration": 5}', "{}"):
            with mock.patch.object(subprocess, "run", self.fake_run(0, out)):
                with self.assertRaises(ValueError):
                    service.probe_source("u")
        with mock.patch.object(subprocess, "run", self.fake_run(1, "")):
            with self.assertRaises(RuntimeError):
                service.probe_source("u")


if __name__ == "__main__":
    unittest.main()
