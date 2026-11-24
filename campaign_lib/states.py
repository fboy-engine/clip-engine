"""Queue state machine for one campaign. Pure logic, no I/O, no clock of its own.

QUEUED -> EDITING -> READY -> AWAITING_APPROVAL -> POSTING -> POSTED -> DONE

- AWAITING_APPROVAL: only the head clip, only when READY and approve_gap_min have passed since the
  previous POSTED time (first clip: no wait). Nothing uploads by itself: POSTING needs the tick.
- POSTED: only with a media id AND a permalink (set at the API answer, never earlier).
- DONE: submit_window_min after POSTED (the manual Reach submit window is over).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

STATES = ("QUEUED", "EDITING", "READY", "AWAITING_APPROVAL", "POSTING", "POSTED", "DONE", "SKIPPED")

ALLOWED = {
    "QUEUED": {"EDITING", "SKIPPED"},
    "EDITING": {"READY", "QUEUED", "SKIPPED"},
    "READY": {"AWAITING_APPROVAL", "EDITING"},
    "AWAITING_APPROVAL": {"POSTING", "READY"},
    "POSTING": {"POSTED", "AWAITING_APPROVAL"},  # back to AWAITING_APPROVAL = upload failed, tick again
    "POSTED": {"DONE"},
    "DONE": set(),
    "SKIPPED": set(),  # failed a gate / no confident cam: the reason is in the note, the queue moves on
}


class StateError(ValueError):
    pass


@dataclass
class Item:
    n: int
    source: str
    kind: str = "clip"  # clip | vod
    state: str = "QUEUED"
    moment: str = ""  # "start-end" seconds in the source
    hook: str = ""
    caption: str = ""
    file: str = ""
    posted_at: Optional[datetime] = None  # tz-aware UTC
    media_id: str = ""
    permalink: str = ""
    note: str = ""
    run: str = ""  # id of the clip.yml run that rendered this item (artifact clip-<run>), or PENDING@<utc>


def transition(item, new, now=None, media_id=None, permalink=None):
    if item.state not in ALLOWED or new not in STATES:
        raise StateError("unknown state %s -> %s" % (item.state, new))
    if new not in ALLOWED[item.state]:
        raise StateError("item %s: %s -> %s is not allowed" % (item.n, item.state, new))
    if new == "READY" and not item.caption:
        raise StateError("item %s: READY needs a caption" % item.n)
    if new == "POSTED":
        if not media_id or not permalink:
            raise StateError("item %s: POSTED needs a media id and a permalink" % item.n)
        if now is None:
            raise StateError("POSTED needs the posted time")
        item.media_id, item.permalink, item.posted_at = str(media_id), permalink, now
    item.state = new
    return item


def unlock_at(items, gap_min):
    """When the next APPROVE may appear: previous posted time + gap. None = no timer yet."""
    posted = [i.posted_at for i in items if i.posted_at]
    return max(posted) + timedelta(minutes=gap_min) if posted else None


def submit_before(item, window_min):
    return item.posted_at + timedelta(minutes=window_min) if item.posted_at else None


def head(items):
    """The clip the campaign is working towards: first one that is not POSTED/DONE/SKIPPED."""
    for i in sorted(items, key=lambda x: x.n):
        if i.state not in ("POSTED", "DONE", "SKIPPED"):
            return i
    return None


def all_used(items):
    return bool(items) and head(items) is None


def next_to_edit(items):
    """Keep editing the next clip while the timer runs: first QUEUED, and only one EDITING at a time."""
    if any(i.state == "EDITING" for i in items):
        return None
    for i in sorted(items, key=lambda x: x.n):
        if i.state == "QUEUED":
            return i
    return None


def plan(items, now, timings, approve_ticked):
    """Decide what to do now. Returns a list of (action, item_n). Pure.

    Actions: DONE, AWAIT (READY -> AWAITING_APPROVAL), POST (AWAITING_APPROVAL -> POSTING),
    CLEAR_TICK (an APPROVE tick that must not count: nothing is waiting for approval)."""
    acts = []
    for i in items:
        if i.state == "POSTED" and now >= submit_before(i, timings["submit_window_min"]):
            acts.append(("DONE", i.n))
    if any(i.state == "POSTING" for i in items):
        return acts  # an upload is in flight; the poster resolves it
    h = head(items)
    if h is None:
        if approve_ticked:
            acts.append(("CLEAR_TICK", None))
        return acts
    if h.state == "AWAITING_APPROVAL":
        if approve_ticked:
            acts.append(("POST", h.n))
        return acts
    if approve_ticked:
        acts.append(("CLEAR_TICK", h.n))  # ticked too early (or stale): never counts
    if h.state == "READY":
        u = unlock_at(items, timings["approve_gap_min"])
        if u is None or now >= u:
            acts.append(("AWAIT", h.n))
    return acts


def apply(items, acts, now):
    """Apply plan() actions that are state changes. POST only moves to POSTING (the upload itself
    lives elsewhere and is never called here)."""
    by_n = {i.n: i for i in items}
    for a, n in acts:
        if a == "DONE":
            transition(by_n[n], "DONE")
        elif a == "AWAIT":
            transition(by_n[n], "AWAITING_APPROVAL")
        elif a == "POST":
            transition(by_n[n], "POSTING")
    return items
