"""Lock decision, handoff decision, watchdog, status line, token warning, log redaction."""
import io
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from campaign_lib import engine, safelog, sheet

NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)
S = lambda secs: sheet.iso(NOW - timedelta(seconds=secs))


def cells(owner="", hb_age=None, handoff="", **kw):
    c = {"lock_run": owner, "heartbeat": S(hb_age) if hb_age is not None else "", "handoff": handoff}
    c.update(kw)
    return c


class LockDecision(unittest.TestCase):
    def test_empty_lock_is_acquired(self):
        self.assertEqual(engine.decide_lock(cells(), "A", NOW), engine.ACQUIRE)

    def test_own_lock_is_held(self):
        self.assertEqual(engine.decide_lock(cells("A", 10), "A", NOW), engine.HOLD)

    def test_second_loop_exits_while_owner_is_live(self):
        self.assertEqual(engine.decide_lock(cells("A", 60), "B", NOW), engine.EXIT)
        self.assertEqual(engine.decide_lock(cells("A", 299), "B", NOW), engine.EXIT)

    def test_dead_owner_is_taken_over(self):
        self.assertEqual(engine.decide_lock(cells("A", 300), "B", NOW), engine.TAKEOVER)
        self.assertEqual(engine.decide_lock(cells("A", 5000), "B", NOW), engine.TAKEOVER)
        self.assertEqual(engine.decide_lock(cells("A", None), "B", NOW), engine.TAKEOVER)  # no heartbeat at all

    def test_handoff_needs_the_matching_token(self):
        c = cells("A", 30, handoff=engine.make_handoff("A", "tok1"))
        self.assertEqual(engine.decide_lock(c, "B", NOW, token="tok1"), engine.ACCEPT_HANDOFF)
        self.assertEqual(engine.decide_lock(c, "B", NOW, token="other"), engine.EXIT)
        self.assertEqual(engine.decide_lock(c, "B", NOW, token=""), engine.EXIT)  # stray duplicate loop
        self.assertEqual(engine.decide_lock(cells("A", 30), "B", NOW, token="tok1"), engine.EXIT)  # no offer written
        # an offer made by an older owner does not open the lock of a newer one
        stale = cells("C", 30, handoff=engine.make_handoff("A", "tok1"))
        self.assertEqual(engine.decide_lock(stale, "B", NOW, token="tok1"), engine.EXIT)

    def test_stop_switch(self):
        self.assertEqual(engine.decide_lock(cells("STOP", 5000), "A", NOW), engine.EXIT)
        self.assertEqual(engine.decide_lock(cells(" stop"[1:].upper(), 5000), "A", NOW), engine.EXIT)
        self.assertFalse(engine.watchdog_should_start(cells("STOP", 5000), NOW))

    def test_lock_cells_clear_the_offer(self):
        c = engine.lock_cells("B", NOW)
        self.assertEqual((c["lock_run"], c["handoff"]), ("B", ""))
        self.assertEqual(c["heartbeat"], c["loop_start"])


class HandoffDecision(unittest.TestCase):
    def test_handoff_at_five_thirty(self):
        start = NOW - timedelta(hours=5, minutes=29, seconds=59)
        self.assertFalse(engine.should_handoff(start, NOW))
        self.assertTrue(engine.should_handoff(start - timedelta(seconds=1), NOW))
        self.assertTrue(engine.should_handoff(NOW - timedelta(hours=6), NOW))

    def test_unknown_start_never_hands_off(self):
        self.assertFalse(engine.should_handoff(None, NOW))
        self.assertFalse(engine.past_hard_stop(None, NOW))

    def test_hard_stop_is_before_the_six_hour_limit(self):
        self.assertLess(engine.HARD_STOP_S, 6 * 3600)
        self.assertGreater(engine.HARD_STOP_S, engine.HANDOFF_AFTER_S)
        self.assertTrue(engine.past_hard_stop(NOW - timedelta(seconds=engine.HARD_STOP_S), NOW))


class Watchdog(unittest.TestCase):
    def test_starts_only_without_a_live_heartbeat(self):
        self.assertTrue(engine.watchdog_should_start(cells(), NOW))
        self.assertTrue(engine.watchdog_should_start(cells("A", 301), NOW))
        self.assertFalse(engine.watchdog_should_start(cells("A", 120), NOW))


class StatusLine(unittest.TestCase):
    TZ = ZoneInfo("Asia/Karachi")

    def test_live_has_no_banner(self):
        self.assertEqual(engine.engine_status(cells("A", 100), NOW, self.TZ), {"live": True, "text": ""})

    def test_offline_since_local_hh_mm(self):
        st = engine.engine_status(cells("A", 6 * 60), NOW, self.TZ)
        self.assertFalse(st["live"])
        self.assertEqual(st["text"], "engine offline since 16:54")  # 11:54 UTC = 16:54 Karachi

    def test_exactly_five_minutes_is_offline(self):
        self.assertFalse(engine.engine_status(cells("A", 300), NOW, self.TZ)["live"])
        self.assertTrue(engine.engine_status(cells("A", 299), NOW, self.TZ)["live"])

    def test_never_started(self):
        self.assertIn("never started", engine.engine_status(cells(), NOW, self.TZ)["text"])


class TokenWarning(unittest.TestCase):
    def w(self, days):
        d = (NOW + timedelta(days=days)).strftime("%Y-%m-%d")
        return engine.token_warning(d, NOW)

    def test_quiet_when_far_or_unknown(self):
        self.assertIsNone(self.w(30))
        self.assertIsNone(self.w(8))
        self.assertIsNone(engine.token_warning("", NOW))
        self.assertIsNone(engine.token_warning(None, NOW))
        self.assertIsNone(engine.token_warning("soon", NOW))

    def test_warns_seven_days_before(self):
        self.assertIn("in 7 days", self.w(7))
        self.assertIn("in 3 days", self.w(3))
        self.assertIn("in 1 day (", self.w(1))
        self.assertIn("today", self.w(0))  # expiry date = today, still valid until 23:59 UTC

    def test_expired(self):
        self.assertIn("EXPIRED", self.w(-1))

    def test_iso_timestamp_form(self):
        self.assertIn("EXPIRED", engine.token_warning("2026-11-01T00:00:00Z", NOW))


class Redaction(unittest.TestCase):
    def test_urls_tokens_ids_and_secret_values_never_survive(self):
        key = "supersecretvalue123"
        t = ("go https://reachclipping.com/Sisters and https://www.instagram.com/reel/ABC/ Bearer abc.def "
             "ghp_" + "a" * 30 + " EAAB" + "z" * 40 + " sheet 1VYtK6mm40NXyB6ixLRoMtsc5yDxFXX9u_FUCcLGLvh8 " + key)
        out = safelog.redact(t, [key])
        for bad in ("reachclipping", "instagram.com", "abc.def", "ghp_", "EAAB", "1VYtK6mm", key):
            self.assertNotIn(bad, out)

    def test_plain_state_text_is_kept(self):
        self.assertEqual(safelog.redact("tick 12 POSTED clip #3 in 41s"), "tick 12 POSTED clip #3 in 41s")

    def test_logger_redacts_fields_and_errors(self):
        buf = io.StringIO()
        log = safelog.Logger(["hunter2hunter2"], stream=buf, clock=lambda: NOW)
        log("EVENT", note="hunter2hunter2 at https://x.example/y")
        log("ERR", e=log.err(ValueError("bad https://x.example/z hunter2hunter2")))
        self.assertNotIn("hunter2", buf.getvalue())
        self.assertNotIn("x.example", buf.getvalue())
        self.assertIn("12:00:00 EVENT", buf.getvalue())

    def test_secret_values_reads_env_and_ignores_short(self):
        env = {"A": ' "tokenvalue1" ', "B": "x", "C": ""}
        self.assertEqual(safelog.secret_values(["A", "B", "C", "D"], env), ["tokenvalue1"])


if __name__ == "__main__":
    unittest.main()
