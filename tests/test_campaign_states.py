import unittest
from datetime import datetime, timedelta, timezone

from campaign_lib import config, states
from campaign_lib.states import Item, StateError

T0 = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
TIM = {"approve_gap_min": 150, "submit_window_min": 30}


def not_done(acts):
    return [a for a in acts if a[0] != "DONE"]


def items(n=3, state="READY"):
    return [Item(i, "u%d" % i, state=state, caption="c%d" % i) for i in range(1, n + 1)]


class TransitionTests(unittest.TestCase):
    def test_happy_path(self):
        i = Item(1, "u")
        for s in ("EDITING",):
            states.transition(i, s)
        i.caption = "cap"
        states.transition(i, "READY")
        states.transition(i, "AWAITING_APPROVAL")
        states.transition(i, "POSTING")
        states.transition(i, "POSTED", now=T0, media_id="123", permalink="https://x/1")
        self.assertEqual((i.media_id, i.permalink, i.posted_at), ("123", "https://x/1", T0))
        states.transition(i, "DONE")
        self.assertEqual(i.state, "DONE")

    def test_all_seven_states_exist(self):
        self.assertEqual(states.STATES, ("QUEUED", "EDITING", "READY", "AWAITING_APPROVAL", "POSTING", "POSTED", "DONE"))

    def test_illegal_jumps(self):
        for frm, to in (("QUEUED", "READY"), ("QUEUED", "POSTING"), ("READY", "POSTING"), ("READY", "POSTED"),
                        ("AWAITING_APPROVAL", "POSTED"), ("POSTED", "QUEUED"), ("DONE", "QUEUED"), ("POSTING", "DONE")):
            i = Item(1, "u", state=frm, caption="c")
            with self.assertRaises(StateError, msg="%s->%s" % (frm, to)):
                states.transition(i, to, now=T0, media_id="1", permalink="p")

    def test_posted_needs_media_id_and_permalink(self):
        for kw in ({}, {"media_id": "1"}, {"permalink": "p"}, {"media_id": "", "permalink": "p"}):
            i = Item(1, "u", state="POSTING")
            with self.assertRaises(StateError):
                states.transition(i, "POSTED", now=T0, **kw)
            self.assertEqual(i.state, "POSTING")
            self.assertIsNone(i.posted_at)

    def test_ready_needs_caption(self):
        i = Item(1, "u", state="EDITING")
        with self.assertRaises(StateError):
            states.transition(i, "READY")

    def test_failed_upload_goes_back_to_awaiting(self):
        i = Item(1, "u", state="POSTING")
        states.transition(i, "AWAITING_APPROVAL")
        self.assertEqual(i.state, "AWAITING_APPROVAL")


class TimerTests(unittest.TestCase):
    def posted(self, minutes_ago, n=1):
        i = Item(n, "u", state="POSTED", caption="c", media_id="m", permalink="p")
        i.posted_at = T0 - timedelta(minutes=minutes_ago)
        return i

    def test_first_clip_has_no_wait(self):
        it = items()
        self.assertIsNone(states.unlock_at(it, 150))
        self.assertEqual(states.plan(it, T0, TIM, False), [("AWAIT", 1)])

    def test_150_minutes_after_previous_posted(self):
        p = self.posted(149)
        nxt = Item(2, "u2", state="READY", caption="c")
        self.assertEqual(not_done(states.plan([p, nxt], T0, TIM, False)), [])
        p = self.posted(150)
        nxt = Item(2, "u2", state="READY", caption="c")
        self.assertEqual(not_done(states.plan([p, nxt], T0, TIM, False)), [("AWAIT", 2)])

    def test_timer_runs_from_latest_posted_time(self):
        a, b = self.posted(300, 1), self.posted(10, 2)
        self.assertEqual(states.unlock_at([a, b], 150), b.posted_at + timedelta(minutes=150))

    def test_only_if_next_clip_ready(self):
        p = self.posted(500)
        for st in ("QUEUED", "EDITING"):
            nxt = Item(2, "u2", state=st)
            self.assertEqual(not_done(states.plan([p, nxt], T0, TIM, False)), [])

    def test_submit_window_30_minutes_then_done(self):
        p = self.posted(29)
        self.assertEqual(states.submit_before(p, 30), T0 + timedelta(minutes=1))
        self.assertEqual(states.plan([p], T0, TIM, False), [])
        p = self.posted(30)
        self.assertEqual(states.plan([p], T0, TIM, False), [("DONE", 1)])


class ApprovalTests(unittest.TestCase):
    def test_tick_posts_only_when_awaiting(self):
        it = items(1, "AWAITING_APPROVAL")
        self.assertEqual(states.plan(it, T0, TIM, True), [("POST", 1)])
        self.assertEqual(states.plan(it, T0, TIM, False), [])

    def test_early_tick_never_counts(self):
        p = Item(1, "u", state="POSTED", caption="c", media_id="m", permalink="p", posted_at=T0 - timedelta(minutes=5))
        nxt = Item(2, "u2", state="READY", caption="c")
        acts = states.plan([p, nxt], T0, TIM, True)
        self.assertIn(("CLEAR_TICK", 2), acts)
        self.assertNotIn(("POST", 2), acts)
        self.assertNotIn(("AWAIT", 2), acts)

    def test_stale_tick_does_not_approve_in_same_tick_as_unlock(self):
        it = items(1, "READY")
        acts = states.plan(it, T0, TIM, True)
        self.assertEqual(acts, [("CLEAR_TICK", 1), ("AWAIT", 1)])
        states.apply(it, acts, T0)
        self.assertEqual(it[0].state, "AWAITING_APPROVAL")  # waits for a NEW tick

    def test_no_second_upload_while_posting(self):
        it = [Item(1, "u", state="POSTING", caption="c"), Item(2, "u2", state="READY", caption="c")]
        self.assertEqual(states.plan(it, T0, TIM, True), [])

    def test_offline_user_everything_waits(self):
        it = items(2, "READY")
        states.apply(it, states.plan(it, T0, TIM, False), T0)
        later = T0 + timedelta(days=3)
        self.assertEqual(states.plan(it, later, TIM, False), [])
        self.assertEqual([i.state for i in it], ["AWAITING_APPROVAL", "READY"])


class QueueTests(unittest.TestCase):
    def test_head_and_all_used(self):
        it = items(2, "READY")
        self.assertEqual(states.head(it).n, 1)
        self.assertFalse(states.all_used(it))
        for i in it:
            i.state = "DONE"
        self.assertTrue(states.all_used(it))
        self.assertFalse(states.all_used([]))

    def test_next_to_edit_only_one_at_a_time(self):
        it = [Item(1, "a", state="EDITING"), Item(2, "b"), Item(3, "c")]
        self.assertIsNone(states.next_to_edit(it))
        it[0].state = "READY"
        it[0].caption = "c"
        self.assertEqual(states.next_to_edit(it).n, 2)

    def test_full_sequence_three_fake_clips(self):
        it = items(3, "READY")
        now = T0
        posted = []
        for _ in range(3):
            states.apply(it, states.plan(it, now, TIM, False), now)
            h = states.head(it)
            self.assertEqual(h.state, "AWAITING_APPROVAL")
            acts = states.plan(it, now, TIM, True)
            states.apply(it, acts, now)
            states.transition(h, "POSTED", now=now, media_id="m%d" % h.n, permalink="p%d" % h.n)
            posted.append(now)
            # not before 150 min
            states.apply(it, states.plan(it, now + timedelta(minutes=149), TIM, False), now)
            nxt = states.head(it)
            if nxt:
                self.assertEqual(nxt.state, "READY")
            now += timedelta(minutes=150)
        self.assertEqual([b - a for a, b in zip(posted, posted[1:])], [timedelta(minutes=150)] * 2)


if __name__ == "__main__":
    unittest.main()
