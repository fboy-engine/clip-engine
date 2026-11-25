"""Loop: lock, heartbeat, handoff decision, and 'a lost lock never uploads'. In-memory sheet, fake clock, no network."""
import unittest
from datetime import datetime, timedelta, timezone

from campaign_lib import engine, loop, poster, runner, sheet
from campaign_lib.states import Item
from tests.test_campaign_poster import CFG, MemIO, fake_item, state_of

T0 = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)
ISO = sheet.iso


def cells(owner="", hb=None, handoff=""):
    return {"lock_run": owner, "heartbeat": ISO(hb) if hb else "", "handoff": handoff}


class LockDecisions(unittest.TestCase):
    def test_fresh_start_acquires(self):
        self.assertEqual(engine.decide_lock(cells(), "A", T0), engine.ACQUIRE)

    def test_live_owner_blocks_a_second_loop(self):
        self.assertEqual(engine.decide_lock(cells("A", T0 - timedelta(seconds=60)), "B", T0), engine.EXIT)

    def test_dead_owner_is_taken_over(self):
        self.assertEqual(engine.decide_lock(cells("A", T0 - timedelta(seconds=engine.STALE_S + 1)), "B", T0), engine.TAKEOVER)

    def test_handoff_needs_the_exact_owner_and_token(self):
        live = T0 - timedelta(seconds=30)
        good = cells("A", live, engine.make_handoff("A", "tok1"))
        self.assertEqual(engine.decide_lock(good, "B", T0, token="tok1"), engine.ACCEPT_HANDOFF)
        self.assertEqual(engine.decide_lock(good, "B", T0, token="other"), engine.EXIT)
        self.assertEqual(engine.decide_lock(good, "B", T0, token=""), engine.EXIT)
        stolen = cells("A", live, engine.make_handoff("Z", "tok1"))
        self.assertEqual(engine.decide_lock(stolen, "B", T0, token="tok1"), engine.EXIT)

    def test_stop_beats_everything(self):
        self.assertEqual(engine.decide_lock(cells("STOP"), "A", T0), engine.EXIT)
        self.assertFalse(engine.watchdog_should_start(cells("STOP"), T0))

    def test_watchdog_starts_only_when_nobody_is_alive(self):
        self.assertTrue(engine.watchdog_should_start(cells("A", T0 - timedelta(minutes=10)), T0))
        self.assertFalse(engine.watchdog_should_start(cells("A", T0 - timedelta(seconds=40)), T0))


class Acquire(unittest.TestCase):
    def test_acquire_writes_the_lock_and_reads_it_back(self):
        io = MemIO([fake_item()])
        ok, how = loop.acquire(io, "A", T0, sleep=lambda s: None)
        self.assertTrue(ok)
        self.assertEqual(io.cells["lock_run"], "A")
        self.assertEqual(io.cells["loop_start"], ISO(T0))
        self.assertEqual(io.cells["handoff"], "")

    def test_second_starter_exits_without_writing(self):
        io = MemIO([fake_item()], {"lock_run": "A", "heartbeat": ISO(T0 - timedelta(seconds=20))})
        ok, _ = loop.acquire(io, "B", T0, sleep=lambda s: None)
        self.assertFalse(ok)
        self.assertEqual(io.writes, 0)
        self.assertEqual(io.cells["lock_run"], "A")

    def test_two_racing_starters_only_one_wins(self):
        io = MemIO([fake_item()])
        # B's write lands between A's write and A's read-back
        def b_writes(m):
            if m.cells.get("lock_run") == "A" and not m.cells.get("b_done"):
                m.cells.update({"lock_run": "B", "b_done": True})
        ok_a, _ = loop.acquire(io, "A", T0, sleep=lambda s: setattr(io, "before_read", b_writes))
        self.assertFalse(ok_a)  # A sees B's id when it reads back
        self.assertEqual(io.cells["lock_run"], "B")

    def test_release_only_when_still_ours(self):
        io = MemIO([fake_item()], {"lock_run": "A"})
        loop.release(io, "A", T0)
        self.assertEqual(io.cells["lock_run"], "")
        io.cells["lock_run"] = "B"
        loop.release(io, "A", T0)
        self.assertEqual(io.cells["lock_run"], "B")

    def test_guard_stops_on_stop_and_on_a_lost_lock(self):
        g = loop.make_guard("A")
        g({"lock_run": "A"}, T0)
        for owner in ("STOP", "B", ""):
            with self.assertRaises(loop.LockLost):
                g({"lock_run": owner}, T0)


class HandoffDecision(unittest.TestCase):
    def test_timeline(self):
        s = T0
        self.assertEqual(loop.decide_step(s, s + timedelta(hours=5, minutes=29), False), "TICK")
        self.assertEqual(loop.decide_step(s, s + timedelta(hours=5, minutes=30), False), "HANDOFF")
        self.assertEqual(loop.decide_step(s, s + timedelta(hours=5, minutes=40), True), "TICK")
        self.assertEqual(loop.decide_step(s, s + timedelta(hours=5, minutes=55), True), "HARD_STOP")
        self.assertEqual(loop.decide_step(s, s + timedelta(hours=5, minutes=55), False), "HARD_STOP")
        self.assertLess(engine.HARD_STOP_S, 6 * 3600)

    def test_slots_alternate_and_a_tick_is_never_faster_than_every_60_s(self):
        self.assertEqual((loop.next_slot("a"), loop.next_slot("b"), loop.next_slot("w")), ("b", "a", "a"))
        self.assertEqual(loop.tick_interval({1: "QUEUED"}), 60)
        for st in ("EDITING", "READY", "AWAITING_APPROVAL", "POSTING"):  # an active queue is not polled faster either
            self.assertEqual(loop.tick_interval({1: "QUEUED", 2: st}), 60)
        self.assertEqual(loop.tick_interval(), loop.TICK_S)


class Clock:
    def __init__(self):
        self.now = T0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += timedelta(seconds=s)


class GH:
    def __init__(self):
        self.calls = []

    def dispatch(self, wf, inputs, ref="main"):
        self.calls.append((wf, dict(inputs)))


class FullRun(unittest.TestCase):
    def test_loop_hands_over_once_at_5h30_and_exits_when_the_successor_takes_the_lock(self):
        io, clock, gh, logs = MemIO([fake_item(state="QUEUED")]), Clock(), GH(), []
        succ = {}

        def tick(now, guard, hb):
            return runner.tick(io, CFG, now, fake=True, guard=guard, extra_values=hb, run_id="A")

        orig = clock.sleep

        def sleep(s):
            orig(s)
            # the successor starts as soon as it was dispatched: it accepts the offer
            if gh.calls and not succ:
                tok = gh.calls[0][1]["handoff_token"]
                ok, how = loop.acquire(io, "B", clock.now, tok, sleep=lambda x: None)
                succ.update(ok=ok, how=how)

        code = loop.run(io, CFG, "A", "a", "", tick, gh, clock, sleep, lambda ev, **kw: logs.append(ev))
        self.assertEqual(code, 0)
        self.assertEqual(len(gh.calls), 1)
        wf, inp = gh.calls[0]
        self.assertEqual((wf, inp["mode"], inp["slot"]), ("campaign-lauren-hamden.yml", "loop", "b"))
        self.assertTrue(succ["ok"])
        self.assertEqual(succ["how"], engine.ACCEPT_HANDOFF)
        self.assertEqual(io.cells["lock_run"], "B")
        self.assertIn("HANDOFF_DISPATCHED", logs)
        self.assertEqual(logs[-1], "LOOP_LOST")
        elapsed = (clock.now - T0).total_seconds()
        self.assertGreaterEqual(elapsed, engine.HANDOFF_AFTER_S)
        self.assertLess(elapsed, engine.HANDOFF_AFTER_S + 120)  # left within a tick or two: overlap, no long double run
        self.assertEqual(set(clock.sleeps[1:]), {60})  # [0] is the 2 s lock settle; every tick sleeps 60 s

    def test_without_a_successor_the_loop_leaves_before_the_6h_limit_and_releases_nothing_it_offered(self):
        io, clock, gh, logs = MemIO([fake_item(state="QUEUED")]), Clock(), GH(), []
        code = loop.run(io, CFG, "A", "a", "", lambda n, g, h: runner.tick(io, CFG, n, fake=True, guard=g, extra_values=h),
                        gh, clock, clock.sleep, lambda ev, **kw: logs.append(ev))
        self.assertEqual(code, 0)
        self.assertEqual(logs[-1], "LOOP_HARD_STOP")
        self.assertLess((clock.now - T0).total_seconds(), 6 * 3600)
        self.assertEqual(len(gh.calls), 1)  # offered once

    def test_dead_dispatch_is_retried_not_spammed(self):
        io, clock, logs = MemIO([fake_item(state="QUEUED")]), Clock(), []

        class Bad(GH):
            def dispatch(self, *a, **k):
                self.calls.append(1)
                raise RuntimeError("HTTP 500")

        gh = Bad()
        loop.run(io, CFG, "A", "a", "", lambda n, g, h: runner.tick(io, CFG, n, fake=True, guard=g, extra_values=h),
                 gh, clock, clock.sleep, lambda ev, **kw: logs.append(ev))
        self.assertGreaterEqual(len(gh.calls), 2)
        self.assertLessEqual(len(gh.calls), 6)  # one try per 5 min between 5h30 and 5h55
        self.assertIn("HANDOFF_FAILED", logs)

    def test_stop_in_the_sheet_ends_the_loop_and_nothing_more_is_written(self):
        io, clock, logs = MemIO([fake_item(state="QUEUED")]), Clock(), []
        n = {"t": 0}

        def tick(now, guard, hb):
            n["t"] += 1
            if n["t"] == 3:
                io.cells["lock_run"] = "STOP"
            return runner.tick(io, CFG, now, fake=True, guard=guard, extra_values=hb)

        code = loop.run(io, CFG, "A", "a", "", tick, GH(), clock, clock.sleep, lambda ev, **kw: logs.append(ev))
        self.assertEqual(code, 0)
        self.assertEqual(logs[-1], "LOOP_LOST")
        self.assertEqual(n["t"], 3)
        self.assertEqual(io.cells["lock_run"], "STOP")

    def test_tick_errors_are_survived_then_given_up(self):
        io, clock, logs = MemIO([fake_item()]), Clock(), []

        def tick(now, guard, hb):
            raise OSError("composio down")

        code = loop.run(io, CFG, "A", "a", "", tick, GH(), clock, clock.sleep, lambda ev, **kw: logs.append(ev))
        self.assertEqual(code, 1)
        self.assertEqual(logs.count("TICK_ERROR"), loop.MAX_TICK_ERRORS)

    def test_heartbeat_is_written_each_tick(self):
        io = MemIO([fake_item(state="QUEUED")], {"lock_run": "A"})
        runner.tick(io, CFG, T0, fake=True, guard=loop.make_guard("A"), extra_values=engine.heartbeat_cells(T0))
        self.assertEqual(io.cells["heartbeat"], ISO(T0))
        runner.tick(io, CFG, T0 + timedelta(seconds=30), fake=True, guard=loop.make_guard("A"),
                    extra_values=engine.heartbeat_cells(T0 + timedelta(seconds=30)))
        self.assertEqual(io.cells["heartbeat"], ISO(T0 + timedelta(seconds=30)))


class LockLostNeverUploads(unittest.TestCase):
    def test_lock_taken_between_intent_and_upload_means_no_upload(self):
        io = MemIO([fake_item()], {"approve": True, "lock_run": "A"})
        reads = {"n": 0}

        def successor_takes_over(m):
            reads["n"] += 1
            if reads["n"] == 2:  # the re-read right after the POSTING intent was written
                m.cells["lock_run"] = "B"

        io.before_read = successor_takes_over
        up = poster.FakeUploader()
        with self.assertRaises(loop.LockLost):
            runner.tick(io, CFG, T0, fake=True, fake_uploader=up, guard=loop.make_guard("A"), run_id="A")
        self.assertEqual(up.calls, 0)
        self.assertEqual(state_of(io).state, "POSTING")  # the intent stays: B reconciles, nothing is double posted

    def test_two_loops_with_the_same_approve_post_exactly_once(self):
        io = MemIO([fake_item()], {"approve": True})
        up = poster.FakeUploader()
        runner.tick(io, CFG, T0, fake=True, fake_uploader=up, run_id="A")
        runner.tick(io, CFG, T0 + timedelta(seconds=1), fake=True, fake_uploader=up, run_id="B")
        self.assertEqual(up.calls, 1)
        self.assertEqual(state_of(io).state, "POSTED")


if __name__ == "__main__":
    unittest.main()
