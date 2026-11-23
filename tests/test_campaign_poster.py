"""Idempotent posting: a crash, a timeout or a second loop must never cause a double post.
Only fake items and a fake uploader are used; no network, no Instagram."""
import copy
import unittest
from datetime import datetime, timedelta, timezone

from campaign_lib import poster, runner, sheet, states
from campaign_lib.states import Item

NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)
CFG = {"timings": {"approve_gap_min": 150, "submit_window_min": 30}, "display_tz": "UTC",
       "caption": {"max_line_chars": 90, "max_extra_hashtags": 5, "default_extra_hashtags": ["#GTA6"],
                   "banned_hashtags": []}, "handle": "LaurenHamden"}


class MemIO:
    """In-memory stand-in for SheetIO. The 'sheet' is what read() returns and write() stores."""

    def __init__(self, items, cells=None):
        self.items = items
        self.cells = {"approve": False, "add_link": "", "add_confirm": False, "log_rows": 1}
        self.cells.update(cells or {})
        self.writes, self.log, self.fail_writes, self.before_read = 0, [], 0, None

    def read(self):
        if self.before_read:
            self.before_read(self)
        return sheet.Snapshot(copy.deepcopy(self.items), dict(self.cells))

    def write(self, now, items=None, values=None, log_lines=None, log_rows=None):
        if self.fail_writes:
            self.fail_writes -= 1
            raise RuntimeError("sheet write failed")
        self.writes += 1
        if items is not None:
            self.items = copy.deepcopy(items)
        self.cells.update(values or {})
        self.log.extend(log_lines or [])
        self.cells["log_rows"] = int(log_rows or 1) + len(log_lines or [])
        return 1


def fake_item(n=1, state="AWAITING_APPROVAL"):
    it = Item(n=n, source="FAKE-%d" % n, kind="fake", state=state)
    it.caption = "cap"
    return it


def state_of(io, n=1):
    return [i for i in io.items if i.n == n][0]


class CountingUploader(poster.FakeUploader):
    pass


class HappyPath(unittest.TestCase):
    def test_posting_is_recorded_before_the_upload_and_clears_the_note(self):
        io = MemIO([fake_item()], {"approve": True})
        seen = {}

        class Spy(poster.FakeUploader):
            def post(self, item, token):
                row = state_of(io)  # what the sheet says at the moment the upload is called
                seen["state"], seen["note"] = row.state, row.note
                return super().post(item, token)

        up = Spy()
        res = runner.tick(io, CFG, NOW, fake=True, fake_uploader=up, run_id="R1-1")
        self.assertEqual(seen["state"], "POSTING")
        self.assertTrue(seen["note"].startswith("POST_INTENT t=p1-"))
        self.assertIn("run=R1-1", seen["note"])
        it = state_of(io)
        self.assertEqual((it.state, it.note, it.media_id), ("POSTED", "", "FAKE-1"))
        self.assertEqual(up.calls, 1)
        self.assertEqual(res["posted"], 1)
        self.assertFalse(io.cells["approve"])
        self.assertIn(("POSTING", "clip #1 (intent recorded before the upload)"), io.log)

    def test_if_recording_posting_fails_nothing_is_uploaded(self):
        io = MemIO([fake_item()], {"approve": True})
        io.fail_writes = 1
        up = poster.FakeUploader()
        with self.assertRaises(RuntimeError):
            runner.tick(io, CFG, NOW, fake=True, fake_uploader=up)
        self.assertEqual(up.calls, 0)
        self.assertEqual(state_of(io).state, "AWAITING_APPROVAL")  # sheet untouched, tick still there

    def test_log_rows_keep_counting_across_the_two_writes(self):
        io = MemIO([fake_item()], {"approve": True})
        runner.tick(io, CFG, NOW, fake=True)
        self.assertEqual(io.cells["log_rows"], 1 + len(io.log))


class CrashAndReconcile(unittest.TestCase):
    def test_ambiguous_upload_is_never_retried_and_reconcile_finds_the_post(self):
        io = MemIO([fake_item()], {"approve": True})

        class SentThenTimeout(poster.FakeUploader):
            def post(self, item, token):
                super().post(item, token)  # the platform got it ...
                raise poster.UploadAmbiguous("read timeout")  # ... but we never heard back

        up = SentThenTimeout()
        runner.tick(io, CFG, NOW, fake=True, fake_uploader=up)
        self.assertEqual(state_of(io).state, "POSTING")
        self.assertEqual(up.calls, 1)
        # the next ticks (same loop, then a restarted one) never call post() again
        runner.tick(io, CFG, NOW + timedelta(seconds=45), fake=True, fake_uploader=up)
        self.assertEqual(up.calls, 1)
        self.assertEqual(state_of(io).state, "POSTED")
        self.assertEqual(state_of(io).media_id, "FAKE-1")
        runner.tick(io, CFG, NOW + timedelta(seconds=90), fake=True, fake_uploader=up)
        self.assertEqual(up.calls, 1)
        self.assertEqual(sum(1 for e, _ in io.log if e == "POSTED"), 0)
        self.assertEqual(sum(1 for e, _ in io.log if e == "RECONCILED_POSTED"), 1)

    def test_process_crash_after_recording_posting_before_the_upload(self):
        io = MemIO([fake_item()], {"approve": True})

        class Crash(poster.FakeUploader):
            def post(self, item, token):
                raise KeyboardInterrupt()  # the runner dies; nothing was sent

        with self.assertRaises(KeyboardInterrupt):
            runner.tick(io, CFG, NOW, fake=True, fake_uploader=Crash())
        it = state_of(io)
        self.assertEqual(it.state, "POSTING")
        self.assertIsNotNone(poster.parse_intent(it.note))
        # restarted loop: not found, too early -> waits, posts nothing
        up = poster.FakeUploader()
        runner.tick(io, CFG, NOW + timedelta(minutes=1), fake=True, fake_uploader=up)
        self.assertEqual((state_of(io).state, up.calls), ("POSTING", 0))
        # after the wait it is conclusive: not posted -> back to AWAITING_APPROVAL, NOT auto-retried
        runner.tick(io, CFG, NOW + timedelta(seconds=poster.RECONCILE_WAIT_S + 5), fake=True, fake_uploader=up)
        self.assertEqual((state_of(io).state, up.calls), ("AWAITING_APPROVAL", 0))
        self.assertIn("tick APPROVE again", state_of(io).note)
        self.assertFalse(io.cells["approve"])
        # only a new APPROVE posts it, exactly once
        io.cells["approve"] = True
        runner.tick(io, CFG, NOW + timedelta(minutes=12), fake=True, fake_uploader=up)
        self.assertEqual((state_of(io).state, up.calls), ("POSTED", 1))

    def test_unknown_lookup_keeps_posting_and_never_posts(self):
        io = MemIO([fake_item()], {"approve": True})

        class Blind(poster.FakeUploader):
            def post(self, item, token):
                raise poster.UploadAmbiguous("timeout")

            def find(self, item, token, since):
                raise ConnectionError("api down")

        up = Blind()
        runner.tick(io, CFG, NOW, fake=True, fake_uploader=up)
        out = runner.tick(io, CFG, NOW + timedelta(hours=2), fake=True, fake_uploader=up)
        self.assertEqual(state_of(io).state, "POSTING")
        self.assertTrue(any(e == "RECONCILE_UNKNOWN" for e, _ in out["notes"]))

    def test_rejected_upload_goes_back_to_awaiting_approval(self):
        io = MemIO([fake_item()], {"approve": True})

        class No(poster.FakeUploader):
            def post(self, item, token):
                raise poster.UploadRejected("caption too long")

        runner.tick(io, CFG, NOW, fake=True, fake_uploader=No())
        it = state_of(io)
        self.assertEqual(it.state, "AWAITING_APPROVAL")
        self.assertIn("rejected", it.note)
        self.assertFalse(io.cells["approve"])

    def test_posting_without_intent_is_left_for_a_human(self):
        it = fake_item(state="POSTING")
        io = MemIO([it])
        up = poster.FakeUploader()
        out = runner.tick(io, CFG, NOW + timedelta(days=1), fake=True, fake_uploader=up)
        self.assertEqual((state_of(io).state, up.calls), ("POSTING", 0))
        self.assertTrue(any(e == "POSTING_NO_INTENT" for e, _ in out["notes"]))


class TwoLoopsRacing(unittest.TestCase):
    def test_lock_lost_between_recording_and_upload_uploads_nothing(self):
        io = MemIO([fake_item()], {"approve": True})
        reads = {"n": 0}

        def guard(cells, now):
            if cells.get("lock_run") != "ME":
                raise RuntimeError("lost the lock")

        io.cells["lock_run"] = "ME"

        def steal_on_second_read(m):
            reads["n"] += 1
            if reads["n"] == 2:  # right after our POSTING write
                m.cells["lock_run"] = "OTHER"

        io.before_read = steal_on_second_read
        up = poster.FakeUploader()
        with self.assertRaises(RuntimeError):
            runner.tick(io, CFG, NOW, fake=True, fake_uploader=up, guard=guard, run_id="ME")
        self.assertEqual(up.calls, 0)
        self.assertEqual(state_of(io).state, "POSTING")  # the new owner reconciles it

    def test_other_loops_token_wins_and_we_do_not_upload_or_overwrite_it(self):
        io = MemIO([fake_item()], {"approve": True})
        reads = {"n": 0}

        def other_loop_wrote(m):
            reads["n"] += 1
            if reads["n"] == 2:
                other = [i for i in m.items if i.n == 1][0]
                other.note = poster.intent_note("p1-theirs", "OTHER-1", NOW)

        io.before_read = other_loop_wrote
        up = poster.FakeUploader()
        runner.tick(io, CFG, NOW, fake=True, fake_uploader=up, run_id="MINE-1")
        self.assertEqual(up.calls, 0)
        self.assertEqual(poster.parse_intent(state_of(io).note)["token"], "p1-theirs")
        self.assertEqual(state_of(io).state, "POSTING")


class RealItemsStayBlocked(unittest.TestCase):
    def test_real_item_never_reaches_posting_or_an_uploader(self):
        it = Item(n=1, source="https://m.twitch.tv/x/clip/AbC", kind="clip", state="AWAITING_APPROVAL")
        it.caption = "cap"
        io = MemIO([it], {"approve": True})

        class Boom(poster.FakeUploader):
            def post(self, item, token):
                raise AssertionError("a real upload was attempted")

        out = runner.tick(io, CFG, NOW, uploader=Boom())  # allow_real_upload not set
        self.assertEqual(state_of(io).state, "AWAITING_APPROVAL")
        self.assertFalse(io.cells["approve"])
        self.assertTrue(any(e == "POST_BLOCKED" for e, _ in out["log"]))

    def test_real_uploader_path_is_idempotent_when_explicitly_enabled(self):
        it = Item(n=1, source="https://m.twitch.tv/x/clip/AbC", kind="clip", state="AWAITING_APPROVAL")
        it.caption = "cap"
        io = MemIO([it], {"approve": True})

        class Real(poster.FakeUploader):
            def post(self, item, token):
                super().post(item, token)
                raise poster.UploadAmbiguous("timeout")

        up = Real()
        runner.tick(io, CFG, NOW, uploader=up, allow_real_upload=True)
        runner.tick(io, CFG, NOW + timedelta(seconds=40), uploader=up, allow_real_upload=True)
        runner.tick(io, CFG, NOW + timedelta(seconds=80), uploader=up, allow_real_upload=True)
        self.assertEqual(up.calls, 1)
        self.assertEqual(state_of(io).state, "POSTED")


class Helpers(unittest.TestCase):
    def test_intent_roundtrip(self):
        n = poster.intent_note("p3-abc", "9-1", NOW)
        self.assertEqual(poster.parse_intent(n), {"token": "p3-abc", "run": "9-1", "at": NOW})
        self.assertIsNone(poster.parse_intent("something else"))
        self.assertIsNone(poster.parse_intent(""))

    def test_tokens_are_unique(self):
        self.assertNotEqual(poster.new_token(1), poster.new_token(1))


if __name__ == "__main__":
    unittest.main()
