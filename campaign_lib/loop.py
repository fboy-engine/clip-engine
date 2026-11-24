"""The self-chaining loop: ONE run ticks every 30-60 s for up to about 5 h 30 min, then hands over to a successor
run it dispatches itself. Nothing here renders: rendering is a separate clip.yml run (editor.py).

Lock, heartbeat, handoff rules live in engine.py (pure). This module is the driver:
  acquire()  : take the sheet lock (fresh start, takeover of a dead loop, or accept a handoff offer) and verify
               by reading it back; a loser exits without ticking
  guard      : on EVERY fresh sheet read: the lock must still be ours (STOP or a successor ends this loop)
  handoff    : at HANDOFF_AFTER_S write an offer "<owner>|<token>" and dispatch the successor with the same token
               into the OTHER concurrency slot (so it starts at once = overlap, no gap). This loop keeps ticking
               until the successor takes the lock (guard raises LockLost) or HARD_STOP_S is reached
  hard stop  : leave before GitHub's 6 h job limit even if no successor came (the cron watchdog restarts a dead loop)
"""
import time
import uuid
from datetime import timedelta

from . import engine

LOOP_WORKFLOW = "campaign-lauren-hamden.yml"
HANDOFF_RETRY_S = 300
MAX_TICK_ERRORS = 20
ACTIVE_STATES = {"EDITING", "READY", "AWAITING_APPROVAL", "POSTING"}


class LockLost(Exception):
    """This loop no longer owns the lock (successor took over, STOP typed, or another loop won)."""


def next_slot(slot):
    """Concurrency slots: a loop in slot a hands over into b and back. A watchdog-started loop (w) goes to a."""
    return "b" if slot == "a" else "a"


def tick_interval(states_by_n):
    """30 s while anything needs quick answers (editing, ready, approval, posting), else 60 s."""
    return 30 if any(s in ACTIVE_STATES for s in (states_by_n or {}).values()) else 60


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
    owner = str(io.read().cells.get("lock_run") or "").strip()
    return (owner == my_id), d if owner == my_id else "lost the race to another starter"


def release(io, my_id, now):
    """Give the lock back on a clean exit, only if it is still ours."""
    if str(io.read().cells.get("lock_run") or "").strip() == my_id:
        io.write(now, values={"lock_run": "", "handoff": ""})


def do_handoff(io, gh, my_id, slot, now):
    """Offer + dispatch. True when the successor was dispatched; False = retry later (the offer stays harmless)."""
    token = uuid.uuid4().hex[:12]
    io.write(now, values={"handoff": engine.make_handoff(my_id, token)})
    gh.dispatch(LOOP_WORKFLOW, {"mode": "loop", "handoff_token": token, "slot": next_slot(slot)})
    return True


def run(io, cfg, my_id, slot, token, tick, gh, now_fn, sleep_fn, log):
    """Returns the process exit code. tick(now, guard, heartbeat) -> the runner.tick summary dict."""
    now = now_fn()
    ok, why = acquire(io, my_id, now, token, sleep_fn)
    if not ok:
        log("LOOP_EXIT", reason=why)
        return 0
    log("LOOP_START", how=why, slot=slot)
    started, offered, next_try, errors = now, False, None, 0
    guard = make_guard(my_id)
    while True:
        now = now_fn()
        step = decide_step(started, now, offered)
        if step == "HARD_STOP":
            log("LOOP_HARD_STOP", offered=offered)
            if not offered:
                release(io, my_id, now)
            return 0
        res = None
        try:
            res = tick(now, guard, engine.heartbeat_cells(now))
            errors = 0
        except LockLost as e:
            log("LOOP_LOST", why=str(e))
            return 0
        except Exception as e:
            errors += 1
            log("TICK_ERROR", n=errors, err=type(e).__name__)
            if errors >= MAX_TICK_ERRORS:
                log("LOOP_GIVE_UP")
                return 1
        if step == "HANDOFF" and (next_try is None or now >= next_try):
            try:
                offered = do_handoff(io, gh, my_id, slot, now)
                log("HANDOFF_DISPATCHED", next_slot=next_slot(slot))
            except Exception as e:
                next_try = now + timedelta(seconds=HANDOFF_RETRY_S)
                log("HANDOFF_FAILED", err=type(e).__name__)
        sleep_fn(tick_interval((res or {}).get("states")))
