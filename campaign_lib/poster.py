"""Idempotent posting. A crash or a timeout in the middle of a post must never cause a second post.

Order of events for one post (runner.tick drives it):
  1. the item moves to POSTING and the sheet records it WITH an intent note (token, run id, time)
     BEFORE anything is uploaded; if that write fails nothing is uploaded;
  2. the sheet is re-read: the lock must still be ours and the intent token must still be ours
     (two loops racing for the same item: only the last writer's token survives);
  3. uploader.post(); success -> POSTED with media id and permalink (never earlier);
       UploadRejected (the API said no, nothing was published) -> back to AWAITING_APPROVAL, tick again;
       anything else (timeout, crash, unknown) -> stays POSTING, NEVER retried blindly;
  4. every tick starts with reconcile(): an item still in POSTING asks uploader.find() whether the post
     exists. Found -> POSTED. Not found for RECONCILE_WAIT_S -> AWAITING_APPROVAL (the person ticks again).
     Cannot tell -> stays POSTING and says so in the Log.

No Instagram code lives here: an Uploader is injected. In this build only FakeUploader exists.
"""
import re
import uuid

from . import sheet, states

RECONCILE_WAIT_S = 600
_INTENT = re.compile(r"^POST_INTENT t=(\S+) run=(\S*) at=(\S+)$")


class UploadRejected(Exception):
    """Definitive: the platform refused, nothing was published."""


class UploadAmbiguous(Exception):
    """The request may or may not have gone through (timeout, connection reset, crash after send)."""


class FakeUploader:
    """No network. Clearly fake ids and links. Remembers tokens so find() can answer."""

    def __init__(self):
        self.posts = {}   # token -> (media_id, permalink)
        self.calls = 0

    def post(self, item, token):
        self.calls += 1
        res = ("FAKE-%d" % item.n, "https://www.instagram.com/reel/FAKE%d/" % item.n)
        self.posts[token] = res
        return res

    def find(self, item, token, since):
        return self.posts.get(token)


def new_token(n):
    return "p%d-%s" % (n, uuid.uuid4().hex[:8])


def intent_note(token, run_id, now):
    return "POST_INTENT t=%s run=%s at=%s" % (token, run_id or "-", sheet.iso(now))


def parse_intent(note):
    m = _INTENT.match(str(note or "").strip())
    if not m:
        return None
    return {"token": m.group(1), "run": m.group(2), "at": sheet.parse_iso(m.group(3))}


def begin(items, now, run_id, moved):
    """Attach an intent note to the items that moved to POSTING in THIS pass (their numbers are in `moved`).
    An item found already in POSTING is never touched here: reconcile() settles it, nothing re-uploads it."""
    fresh = []
    for i in items:
        if i.n in moved and i.state == "POSTING":
            i.note = intent_note(new_token(i.n), run_id, now)
            fresh.append(i)
    return fresh


def still_mine(snapshot_items, item):
    """After the POSTING write: is the sheet's intent for this item still the one we wrote?"""
    mine = parse_intent(item.note)
    for i in snapshot_items:
        if i.n == item.n:
            theirs = parse_intent(i.note)
            return bool(mine and theirs and i.state == "POSTING" and theirs["token"] == mine["token"])
    return False


def execute(item, uploader, now, log):
    """One upload attempt for an item that has an intent. Only Exception is caught: a real crash
    (BaseException) leaves POSTING + intent in the sheet for reconcile() to settle."""
    intent = parse_intent(item.note)
    try:
        media_id, permalink = uploader.post(item, intent["token"])
    except UploadRejected as e:
        states.transition(item, "AWAITING_APPROVAL")
        item.note = "upload rejected, tick APPROVE again: %s" % str(e)[:80]
        log.append(("POST_REJECTED", "clip #%d" % item.n))
        return "rejected"
    except Exception as e:  # unknown outcome: never retry here, reconcile decides
        log.append(("POST_UNCERTAIN", "clip #%d %s (will reconcile, no retry)" % (item.n, type(e).__name__)))
        return "uncertain"
    states.transition(item, "POSTED", now=now, media_id=media_id, permalink=permalink)
    item.note = ""
    log.append(("POSTED", "clip #%d" % item.n))
    return "posted"


def reconcile(items, uploader, now, log, wait_s=RECONCILE_WAIT_S, notes=None):
    """Settle items left in POSTING by an earlier run. Returns the numbers of items it settled.
    Outcomes that repeat every tick (still waiting, cannot tell) go to `notes` (stdout only), not the sheet Log."""
    settled = []
    notes = [] if notes is None else notes
    for i in items:
        if i.state != "POSTING":
            continue
        intent = parse_intent(i.note)
        if intent is None:
            notes.append(("POSTING_NO_INTENT", "clip #%d stays POSTING (no intent note, check by hand)" % i.n))
            continue
        if uploader is None:
            notes.append(("RECONCILE_WAITING", "clip #%d: no uploader enabled, stays POSTING" % i.n))
            continue
        try:
            found = uploader.find(i, intent["token"], intent["at"])
        except Exception as e:
            notes.append(("RECONCILE_UNKNOWN", "clip #%d cannot tell (%s), stays POSTING" % (i.n, type(e).__name__)))
            continue
        if found:
            states.transition(i, "POSTED", now=now, media_id=found[0], permalink=found[1])
            i.note = ""
            log.append(("RECONCILED_POSTED", "clip #%d was posted before the restart" % i.n))
            settled.append(i.n)
        elif intent["at"] is not None and (now - intent["at"]).total_seconds() >= wait_s:
            states.transition(i, "AWAITING_APPROVAL")
            i.note = "reconciled: not posted, tick APPROVE again"
            log.append(("RECONCILED_NOT_POSTED", "clip #%d was not posted, waiting for a new APPROVE" % i.n))
            settled.append(i.n)
        else:
            notes.append(("RECONCILE_WAITING", "clip #%d: not found yet, will look again" % i.n))
    return settled
