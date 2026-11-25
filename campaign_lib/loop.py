"""The self-chaining loop: ONE run ticks once a minute (never faster) for up to about 5 h 30 min, then hands over to a
successor run it dispatches itself. Nothing here renders: rendering is a separate clip.yml run (editor.py).

Lock, heartbeat, handoff rules live in engine.py (pure). This module is the driver:
  acquire()  : take the sheet lock (fresh start, takeover of a dead loop, or accept a handoff offer) and verify
               by reading it back; a loser exits without ticking
  guard      : on EVERY fresh sheet read: the lock must still be ours (STOP or a successor ends this loop)
  handoff    : at HANDOFF_AFTER_S write an offer "<owner>|<token>" and dispatch the successor with the same token
               into the OTHER concurrency slot (so it starts at once = overlap, no gap). This loop keeps ticking
               until the successor takes the lock (guard raises LockLost) or HARD_STOP_S is reached
  hard stop  : leave before GitHub's 6 h job limit even if no successor came (the cron watchdog restarts a dead loop)

Sheet quota: a quota answer from Google never fails the loop. sheet.SheetIO waits (exponential back-off + jitter)
and retries; when it gives up for this tick (quota.QuotaWait) the tick is deferred, not counted as an error, and the
loop tries again on the next one. Only real errors count towards MAX_TICK_ERRORS.
"""
import time
import uuid
from datetime import timedelta

from . import engine, quota, sheet

LOOP_WORKFLOW = "campaign-lauren-hamden.yml"
HANDOFF_RETRY_S = 300
MAX_TICK_ERRORS = 20
TICK_S = 60               # one tick per minute at most: one sheet read per tick
STATS_EVERY_S = 600       # SHEET_STATS line: requests actually sent, cache hits, quota waits, peak reads/min
ACQUIRE_ROUNDS = 10       # quota-deferred lock attempts at start before the run leaves (exit 0, the watchdog retries)


class LockLost(Exception):
    """This loop no longer owns the lock (successor took over, STOP typed, or another loop won)."""


def next_slot(slot):
    """Concurrency slots: a loop in slot a hands over into b and back. A watchdog-started loop (w) goes to a."""
    return "b" if slot == "a" else "a"


def tick_interval(states_by_n=None):
    """Seconds to sleep after a tick: always TICK_S. The sheet read quota is shared and nothing in the flow needs
    an answer faster than a minute (APPROVE is a tap), so even an active queue is not polled faster."""
    return TICK_S


def decide_step(started, now, handoff_done):
    """What the loop does after a tick: HARD_STOP | HANDOFF | TICK. Pure."""
    if engine.past_hard_stop(started, now):
        return "HARD_STOP"
    if not handoff_done and engine.should_handoff(started, now):
        return "HANDOFF"
    return "TICK"


def make_guard(my_id):
    def guard(cells, now):
        owner = str(cells.get("lock_run") or "").strip()
        if owner == engine.STOP:
            raise LockLost("STOP is typed in the sheet")
        if owner != my_id:
            raise LockLost("the lock is held by another loop")
    return guard


def acquire(io, my_id, now, token="", sleep=time.sleep, settle_s=2):
    """(ok, reason). Takes the lock and reads it back after a short settle so two racing starters see each other."""
    d = engine.decide_lock(io.read().cells, my_id, now, token)
    if d == engine.EXIT:
        return False, "another loop is alive (or STOP)"
    if d != engine.HOLD:
        io.write(now, values=engine.lock_cells(my_id, now, token))
    sleep(settle_s)
    owner = str(sheet.read_fresh(io).cells.get("lock_run") or "").strip()  # the read-back must see the sheet NOW
    return (owner == my_id), d if owner == my_id else "lost the race to another starter"


def acquire_waiting(io, my_id, now_fn, token, sleep, log, rounds=ACQUIRE_ROUNDS):
    """acquire(), but a quota-deferred attempt is simply tried again (the lock write is idempotent: a second try
    sees our own id and HOLDs). After `rounds` deferrals: (False, reason) - the run leaves with exit 0."""
    for i in range(rounds):
        try:
            return acquire(io, my_id, now_fn(), token, sleep)
        except quota.QuotaWait:
            log("QUOTA_DEFERRED", where="acquire", n=i + 1)
            sleep(quota.backoff_delay(99))  # a full cap (30-60 s): the next round must not restart the back-off at 2 s
    return False, "sheet quota still exceeded; the watchdog starts a loop later"


def release(io, my_id, now):
    """Give the lock back on a clean exit, only if it is still ours."""
    if str(sheet.read_fresh(io).cells.get("lock_run") or "").strip() == my_id:
        io.write(now, values={"lock_run": "", "handoff": ""})


def log_stats(io, log):
    """SHEET_STATS: requests really sent to the sheet (reads, writes), cache hits, quota waits, peak reads/min."""
    summary = getattr(io, "summary", None)
    if summary:
        log("SHEET_STATS", **summary())


def do_handoff(io, gh, my_id, slot, now):
    """Offer + dispatch. True when the successor was dispatched; False = retry later (the offer stays harmless)."""
    token = uuid.uuid4().hex[:12]
    io.write(now, values={"handoff": engine.make_handoff(my_id, token)})
    gh.dispatch(LOOP_WORKFLOW, {"mode": "loop", "handoff_token": token, "slot": next_slot(slot)})
    return True


def run(io, cfg, my_id, slot, token, tick, gh, now_fn, sleep_fn, log):
    """Returns the process exit code. tick(now, guard, heartbeat) -> the runner.tick summary dict."""
    now = now_fn()
    ok, why = acquire_waiting(io, my_id, now_fn, token, sleep_fn, log)
    if not ok:
        log("LOOP_EXIT", reason=why)
        return 0
    log("LOOP_START", how=why, slot=slot)
    started, offered, next_try, errors, deferred = now, False, None, 0, 0
    next_stats = now + timedelta(seconds=STATS_EVERY_S)
    guard = make_guard(my_id)
    while True:
        now = now_fn()
        step = decide_step(started, now, offered)
        if step == "HARD_STOP":
            log_stats(io, log)
            log("LOOP_HARD_STOP", offered=offered)
            if not offered:
                try:
                    release(io, my_id, now)
                except quota.QuotaWait:
                    log("RELEASE_SKIPPED", reason="sheet quota")  # the heartbeat goes stale by itself: same effect
            return 0
        res = None
        try:
            res = tick(now, guard, engine.heartbeat_cells(now))
            errors = deferred = 0
        except LockLost as e:
            log_stats(io, log)
            log("LOOP_LOST", why=str(e))
            return 0
        except quota.QuotaWait:
            deferred += 1  # waiting for Google is not a failure: no TICK_ERROR, no give-up counter
            log("QUOTA_DEFERRED", where="tick", n=deferred)
        except Exception as e:
            errors += 1
            log("TICK_ERROR", n=errors, err=type(e).__name__)
            if errors >= MAX_TICK_ERRORS:
                log_stats(io, log)
                log("LOOP_GIVE_UP")
                return 1
        if step == "HANDOFF" and (next_try is None or now >= next_try):
            try:
                offered = do_handoff(io, gh, my_id, slot, now)
                log("HANDOFF_DISPATCHED", next_slot=next_slot(slot))
            except Exception as e:
                next_try = now + timedelta(seconds=HANDOFF_RETRY_S)
                log("HANDOFF_FAILED", err=type(e).__name__)
        if now >= next_stats:
            log_stats(io, log)
            next_stats = now + timedelta(seconds=STATS_EVERY_S)
        sleep_fn(tick_interval((res or {}).get("states")))
