"""The loop through Google quota outages, against SheetIO + a fake client that answers with the quota error.
The clock is fake (6 h run in milliseconds); SheetIO sleeps on the same clock. No network."""
import unittest
from datetime import datetime, timedelta, timezone

from campaign_lib import engine, loop, runner, sheet
from tests.test_campaign_loop import Clock, GH
from tests.test_campaign_poster import CFG
from tests.test_campaign_sheetio import FakeSheetClient

T0 = datetime(2026, 11, 25, 1, 0, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


class Harness:
    def __init__(self, outage=None):
        """outage = (from_min, to_min) of the run, or None. to_min=None means 'never ends'."""
        self.clk, self.logs = Clock(), []
        self.clk.now = T0  # the shared Clock starts on another day: pin it to this module's T0
        self.client = FakeSheetClient(down=(lambda: self._down(outage)) if outage else None)
        self.io = sheet.SheetIO(self.client, "SHEET", max_wait_s=120, rand=lambda: 0.0, sleep=self.clk.sleep,
                                clock=lambda: (self.clk.now - T0).total_seconds())

    def _down(self, outage):
        lo, hi = outage
        return self.clk.now >= at(lo) and (hi is None or self.clk.now < at(hi))

    def run(self):
        def tick(now, guard, hb):
            return runner.tick(self.io, CFG, now, fake=True, guard=guard, extra_values=hb, run_id="A")
        self.code = loop.run(self.io, CFG, "A", "a", "", tick, GH(), self.clk, self.clk.sleep,
                             lambda ev, **kw: self.logs.append(ev))
        return self

    @property
    def minutes(self):
        return (self.clk.now - T0).total_seconds() / 60.0


class QuietRun(unittest.TestCase):
    def test_a_full_loop_stays_far_below_the_quota(self):
        h = Harness().run()
        s = h.io.summary()
        self.assertEqual(h.code, 0)
        self.assertEqual(h.logs[-1], "LOOP_HARD_STOP")
        self.assertEqual(s["quota_waits"], 0)
        self.assertLessEqual(s["peak_reads_per_min"], 2)           # start-up: lock read + read-back
        self.assertLessEqual(s["reads"] / h.minutes, 1.1)          # about one read per minute over the whole run
        self.assertGreater(h.minutes, 5 * 60 + 30)
        self.assertIn("SHEET_STATS", h.logs)                       # the numbers are printed during the run

    def test_the_first_tick_reuses_the_lock_read_back(self):
        h = Harness()
        ok, _ = loop.acquire(h.io, "A", T0, sleep=lambda s: None)
        self.assertTrue(ok)
        before = h.client.n("GOOGLESHEETS_BATCH_GET")
        runner.tick(h.io, CFG, T0, fake=True, guard=loop.make_guard("A"), run_id="A")
        self.assertEqual(h.client.n("GOOGLESHEETS_BATCH_GET"), before)  # served from memory, no extra read


class Outages(unittest.TestCase):
    def assert_never_failed(self, h):
        self.assertEqual(h.code, 0)
        self.assertNotIn("TICK_ERROR", h.logs)
        self.assertNotIn("LOOP_GIVE_UP", h.logs)

    def test_quota_exhausted_for_the_first_10_minutes_the_loop_waits_and_then_runs(self):
        h = Harness(outage=(0, 10)).run()
        self.assert_never_failed(h)
        self.assertIn("QUOTA_DEFERRED", h.logs)
        self.assertIn("LOOP_START", h.logs)
        self.assertEqual(h.logs[-1], "LOOP_HARD_STOP")
        self.assertLessEqual(h.io.summary()["peak_reads_per_min"], 6)  # back-off keeps even the retries gentle

    def test_quota_exhausted_for_20_minutes_in_the_middle_the_ticks_are_deferred_not_failed(self):
        h = Harness(outage=(60, 80)).run()
        self.assert_never_failed(h)
        self.assertEqual(h.logs.count("LOOP_START"), 1)
        self.assertIn("QUOTA_DEFERRED", h.logs)
        self.assertEqual(h.client.cells["lock_run"], "A")       # the loop kept its lock and went on
        self.assertEqual(h.logs[-1], "LOOP_HARD_STOP")
        self.assertLessEqual(h.io.summary()["peak_reads_per_min"], 6)

    def test_an_outage_that_never_ends_at_the_start_exits_zero_so_the_watchdog_can_retry(self):
        h = Harness(outage=(0, None)).run()
        self.assert_never_failed(h)
        self.assertEqual(h.logs[-1], "LOOP_EXIT")
        self.assertNotIn("LOOP_START", h.logs)

    def test_an_outage_from_hour_one_to_the_end_never_fails_the_run_even_with_more_than_20_deferred_ticks(self):
        h = Harness(outage=(60, None)).run()
        self.assert_never_failed(h)
        self.assertGreater(h.logs.count("QUOTA_DEFERRED"), loop.MAX_TICK_ERRORS)
        self.assertIn("RELEASE_SKIPPED", h.logs)                 # could not give the lock back: it goes stale by itself
        self.assertLess(h.minutes, 6 * 60)                       # still left before GitHub's 6 h limit
        self.assertLessEqual(h.io.summary()["peak_reads_per_min"], 6)

    def test_real_errors_still_count_and_still_end_the_run(self):
        h = Harness()
        h.client.other_error = None
        n = {"t": 0}

        def tick(now, guard, hb):
            n["t"] += 1
            raise OSError("composio down")
        code = loop.run(h.io, CFG, "A", "a", "", tick, GH(), h.clk, h.clk.sleep, lambda ev, **kw: h.logs.append(ev))
        self.assertEqual(code, 1)
        self.assertEqual(h.logs.count("TICK_ERROR"), loop.MAX_TICK_ERRORS)


if __name__ == "__main__":
    unittest.main()
