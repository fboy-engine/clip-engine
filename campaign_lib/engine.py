"""Loop lock, heartbeat, handoff and token-expiry rules. Pure logic: no I/O, no clock of its own.

The sheet holds the lock (Campaign cells lock_run, heartbeat, handoff, loop_start). A loop is LIVE while
its heartbeat is younger than STALE_S. One loop owns the lock; a second one exits. Near the end of its
life (HANDOFF_AFTER_S) the owner writes a handoff offer "<owner>|<token>" and dispatches a successor that
carries the same token. Only that successor may take the lock from a live owner, then the old loop sees
the lock is gone at its next tick and exits: the two overlap, there is no gap.
"""
from datetime import datetime, timedelta, timezone

from . import sheet

STALE_S = 300                     # heartbeat older than this = engine offline (panel + watchdog + takeover)
HANDOFF_AFTER_S = 5 * 3600 + 30 * 60   # dispatch the successor
HARD_STOP_S = 5 * 3600 + 55 * 60       # leave before GitHub's 6 h job limit even if no successor came
TOKEN_WARN_DAYS = 7
STOP = "STOP"                     # typed into lock_run by the user: nothing starts, every loop exits

ACQUIRE, HOLD, TAKEOVER, ACCEPT_HANDOFF, EXIT = "ACQUIRE", "HOLD", "TAKEOVER", "ACCEPT_HANDOFF", "EXIT"


def make_handoff(owner, token):
    return "%s|%s" % (owner, token)


def is_live(heartbeat, now, stale_s=STALE_S):
    return heartbeat is not None and (now - heartbeat).total_seconds() < stale_s


def decide_lock(cells, my_id, now, token="", stale_s=STALE_S):
    """What may loop `my_id` do with the lock it just read? Pure."""
    owner = str(cells.get("lock_run") or "").strip()
    if owner == STOP:
        return EXIT
    if not owner:
        return ACQUIRE
    if owner == my_id:
        return HOLD
    if not is_live(sheet.parse_iso(cells.get("heartbeat")), now, stale_s):
        return TAKEOVER  # the owner died (no heartbeat)
    offer = str(cells.get("handoff") or "").strip()
    if token and offer == make_handoff(owner, token):
        return ACCEPT_HANDOFF
    return EXIT  # another live loop owns it


def watchdog_should_start(cells, now, stale_s=STALE_S):
    """The cron watchdog starts a loop only when nobody is alive (and nobody pressed STOP)."""
    if str(cells.get("lock_run") or "").strip() == STOP:
        return False
    return not is_live(sheet.parse_iso(cells.get("heartbeat")), now, stale_s)


def age_s(start, now):
    return None if start is None else (now - start).total_seconds()


def should_handoff(loop_start, now, after_s=HANDOFF_AFTER_S):
    a = age_s(loop_start, now)
    return a is not None and a >= after_s


def past_hard_stop(loop_start, now, hard_s=HARD_STOP_S):
    a = age_s(loop_start, now)
    return a is not None and a >= hard_s


def lock_cells(my_id, now, token=""):
    """Cells written when a loop takes the lock (also clears any handoff offer)."""
    t = sheet.iso(now)
    return {"lock_run": my_id, "heartbeat": t, "loop_start": t, "handoff": ""}


def heartbeat_cells(now):
    return {"heartbeat": sheet.iso(now)}


def engine_status(cells, now, tz, stale_s=STALE_S):
    """The panel line. Same rule as engineStatus_ in docs/apps_script/lauren/Code.gs."""
    hb = sheet.parse_iso(cells.get("heartbeat"))
    if hb is None:
        return {"live": False, "text": "engine offline (never started)"}
    if is_live(hb, now, stale_s):
        return {"live": True, "text": ""}
    return {"live": False, "text": "engine offline since %s" % hb.astimezone(tz).strftime("%H:%M")}


def _parse_expiry(raw):
    if raw in (None, ""):
        return None
    s = str(raw).strip()
    for fmt, end_of_day in (("%Y-%m-%d", True), ("%Y-%m-%dT%H:%M:%SZ", False)):
        try:
            d = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return d + timedelta(hours=23, minutes=59, seconds=59) if end_of_day else d
    return None


def token_warning(raw_expiry, now, warn_days=TOKEN_WARN_DAYS):
    """None when unknown or far away; otherwise the text for the panel. Counted in calendar days (UTC),
    so "7 days before" means the warning is on from the same weekday a week earlier.
    Same rule as tokenWarning_ in docs/apps_script/lauren/Code.gs."""
    exp = _parse_expiry(raw_expiry)
    if exp is None:
        return None
    if now > exp:
        return "Instagram token EXPIRED - renew it before the next post"
    days = (exp.date() - now.astimezone(timezone.utc).date()).days
    if days <= warn_days:
        when = "today" if days == 0 else "in %d day%s" % (days, "" if days == 1 else "s")
        return "Instagram token expires %s (%s)" % (when, exp.strftime("%Y-%m-%d"))
    return None
