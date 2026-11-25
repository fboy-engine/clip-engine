"""Watchdog, CLI and service wiring through Google quota outages (fake sheet client, fake clock, no network)."""
import io as _io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

from campaign_lib import cli, composio, service, sheet
from tests.test_campaign_loop import Clock, GH
from tests.test_campaign_sheetio import FakeSheetClient
from tests.test_campaign_workflow import CFG2

T0 = datetime(2026, 11, 25, 1, 0, 0, tzinfo=timezone.utc)


def make_io(client, clk=None, max_wait_s=150, **kw):
    clk = clk or Clock()
    clk.now = T0
    return sheet.SheetIO(client, "SHEET", max_wait_s=max_wait_s, rand=lambda: 0.0, sleep=clk.sleep,
                         clock=lambda: (clk.now - T0).total_seconds(), **kw), clk


class WatchdogInAnOutage(unittest.TestCase):
    def test_quota_that_does_not_clear_is_a_clean_exit_zero_and_starts_nothing(self):
        client = FakeSheetClient(down=lambda: True)
        io, _ = make_io(client)
        gh, logs = GH(), []
        code = service.run_watchdog(CFG2, io, gh, T0, lambda ev, **kw: logs.append((ev, kw)))
        self.assertEqual(code, 0)
        self.assertEqual(gh.calls, [])  # no blind dispatch: without the sheet it cannot know if a loop is alive
        self.assertEqual(logs[-1][0], "WATCHDOG")
        self.assertEqual(logs[-1][1]["action"], "none")
        self.assertIn("quota", logs[-1][1]["reason"])
        self.assertIn("QUOTA_WAIT", [e for e, _ in logs])

    def test_quota_that_clears_while_waiting_lets_the_watchdog_do_its_job(self):
        client = FakeSheetClient(read_quota=3)  # nobody has ever started a loop: the sheet is blank
        io, clk = make_io(client)
        gh, logs = GH(), []
        code = service.run_watchdog(CFG2, io, gh, T0, lambda ev, **kw: logs.append((ev, kw)))
        self.assertEqual(code, 0)
        self.assertEqual(len(gh.calls), 1)  # waited 17.5 s, read OK, no live loop, started one
        self.assertEqual(clk.sleeps, [2.5, 5.0, 10.0])
        self.assertEqual([kw["attempt"] for e, kw in logs if e == "QUOTA_WAIT"], [1, 2, 3])

    def test_a_live_loop_is_left_alone_after_a_quota_wait(self):
        client = FakeSheetClient(read_quota=2)
        client.cells.update({"lock_run": "A", "heartbeat": sheet.iso(T0 - timedelta(seconds=40))})
        io, _ = make_io(client)
        gh = GH()
        self.assertEqual(service.run_watchdog(CFG2, io, gh, T0, lambda ev, **kw: None), 0)
        self.assertEqual(gh.calls, [])

    def test_a_real_sheet_error_is_still_not_swallowed(self):
        client = FakeSheetClient(other_error="HTTP 401 on POST /x: Invalid API key")
        io, _ = make_io(client)
        with self.assertRaises(composio.ComposioError):
            service.run_watchdog(CFG2, io, GH(), T0, lambda ev, **kw: None)


class CliInAnOutage(unittest.TestCase):
    def main(self, argv, client):
        io, _ = make_io(client)
        out = _io.StringIO()
        with mock.patch.object(cli, "_io", return_value=io), mock.patch.object(service, "new_github", return_value=GH()):
            with redirect_stdout(out):
                code = cli.main(argv)
        return code, out.getvalue()

    def test_watchdog_tick_and_init_exit_zero_on_quota(self):
        for argv in (["watchdog"], ["tick", "--fake"], ["init", "--fake", "2"]):
            code, out = self.main(argv, FakeSheetClient(down=lambda: True))
            self.assertEqual(code, 0, argv)
            self.assertIn("QUOTA", out)
            self.assertNotIn("ERROR", out)

    def test_other_errors_still_exit_one(self):
        code, out = self.main(["tick", "--fake"], FakeSheetClient(other_error="HTTP 401 on POST /x: Invalid API key"))
        self.assertEqual(code, 1)
        self.assertIn("ERROR", out)

    def test_each_mode_has_a_wait_budget_that_fits_its_job(self):
        self.assertLess(cli.WAIT_S["watchdog"], 5 * 60)  # the watchdog job times out at 5 min
        self.assertLess(cli.WAIT_OTHER_S, 10 * 60)       # helper jobs time out at 10 min
        a = mock.Mock(sheet_id="SID", cmd="watchdog")
        with mock.patch.object(composio, "Client", return_value=object()):
            self.assertEqual(cli._io({"sheet": {}}, a).max_wait_s, cli.WAIT_S["watchdog"])
            a.cmd = "loop"
            self.assertEqual(cli._io({"sheet": {}}, a).max_wait_s, cli.WAIT_S["loop"])
            a.cmd = "tick"
            self.assertEqual(cli._io({"sheet": {}}, a).max_wait_s, cli.WAIT_OTHER_S)


class LoopServicePrintsTheWaits(unittest.TestCase):
    def test_run_loop_logs_each_quota_wait_and_still_finishes_with_exit_zero(self):
        client = FakeSheetClient(down=lambda: False)
        clk = Clock()
        io, _ = make_io(client, clk)
        client.down = lambda: clk.now < T0 + timedelta(minutes=5)
        from tests.test_campaign_poster import fake_item
        client.items = [fake_item(state="QUEUED")]
        logs = []
        code = service.run_loop(CFG2, io, GH(), "a", "", fake=True, now_fn=clk, sleep_fn=clk.sleep,
                                log=lambda ev, **kw: logs.append((ev, kw)), environ={"GITHUB_RUN_ID": "9", "GITHUB_RUN_ATTEMPT": "1"})
        events = [e for e, _ in logs]
        self.assertEqual(code, 0)
        self.assertIn("QUOTA_WAIT", events)
        self.assertNotIn("TICK_ERROR", events)
        waits = [kw for e, kw in logs if e == "QUOTA_WAIT"]
        self.assertTrue(all(w["op"] in ("read", "write") and w["wait_s"] >= 2 for w in waits))
        self.assertEqual(events[-1], "LOOP_HARD_STOP")
        stats = [kw for e, kw in logs if e == "SHEET_STATS"][-1]
        self.assertGreater(stats["quota_waits"], 0)
        self.assertLessEqual(stats["peak_reads_per_min"], 6)


if __name__ == "__main__":
    unittest.main()
