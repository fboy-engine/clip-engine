"""One short pass of the campaign loop: read the sheet, decide (states.plan), write the sheet.

B3 scope: NO Instagram code exists here. Only items of kind "fake" are ever moved to POSTING/POSTED
(with a clearly fake permalink). A real item that gets an APPROVE tick is left alone, the tick is
cleared and the log says the uploader is not enabled."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import caption as capmod
from . import poster, sheet, states
from .states import Item


def local(dt, tz, now):
    d = dt.astimezone(tz)
    n = now.astimezone(tz)
    return d.strftime("%H:%M") if d.date() == n.date() else d.strftime("%a %H:%M")


def left(delta):
    m = max(0, int(delta.total_seconds() // 60))
    return "%dh %02dm" % (m // 60, m % 60) if m >= 60 else "%d min" % m


def render_cells(items, now, timings, tz, sheet_title=None):
    """The Campaign box (everything except the two cells the user edits: APPROVE, Add link)."""
    h = states.head(items)
    unlock = states.unlock_at(items, timings["approve_gap_min"])
    posted = [i for i in items if i.posted_at]
    last = max(posted, key=lambda i: i.posted_at) if posted else None
    c = {"next": "", "caption": "", "timer": "-", "opens": "", "link": "", "submit": ""}
    if h is None:
        c["state"] = "ALL USED - paste the next stream link in 'Add link' and tick confirm"
        c["timer"] = "-"
    else:
        c["next"] = "#%d %s (%s)" % (h.n, h.kind, h.source[-60:])
        c["caption"] = h.caption
        if h.state == "AWAITING_APPROVAL":
            c["state"] = "READY - tick APPROVE to post clip #%d" % h.n
            c["timer"] = "APPROVE is open now"
        elif h.state == "READY":
            if unlock and now < unlock:
                c["state"] = "WAITING - clip #%d is ready, APPROVE opens at %s" % (h.n, local(unlock, tz, now))
                c["timer"] = "APPROVE opens in %s" % left(unlock - now)
                c["opens"] = local(unlock, tz, now)
            else:
                c["state"] = "READY - clip #%d" % h.n
        elif h.state == "POSTING":
            c["state"] = "POSTING clip #%d ..." % h.n
        else:
            c["state"] = "EDITING - clip #%d (%s)" % (h.n, h.state)
            if unlock and now < unlock:
                c["timer"] = "next APPROVE not before %s (%s)" % (local(unlock, tz, now), left(unlock - now))
                c["opens"] = local(unlock, tz, now)
    if last:
        c["link"] = last.permalink
        sb = states.submit_before(last, timings["submit_window_min"])
        if now < sb:
            c["submit"] = "submit to Reach before %s" % local(sb, tz, now)
        else:
            c["submit"] = "Reach window for clip #%d closed (was %s)" % (last.n, local(sb, tz, now))
    c["updated"] = local(now, tz, now) + " (" + now.astimezone(tz).strftime("%Y-%m-%d") + ")"
    c["unlock_utc"] = sheet.iso(unlock) if unlock else ""
    return c


class Router:
    """Fake items go to the fake uploader. A real item only ever reaches a real uploader when
    allow_real is set (never in this build: the CLI does not pass it)."""

    def __init__(self, fake, real=None, allow_real=False):
        self.fake, self.real, self.allow_real = fake, real, allow_real

    def _pick(self, item):
        if item.kind == "fake":
            return self.fake
        if self.allow_real and self.real is not None:
            return self.real
        raise RuntimeError("uploader not enabled")

    def post(self, item, token):
        return self._pick(item).post(item, token)

    def find(self, item, token, since):
        return self._pick(item).find(item, token, since)


def tick(io, cfg, now, fake=False, timings=None, guard=None, extra_values=None, extra_log=None,
         uploader=None, fake_uploader=None, allow_real_upload=False, run_id=""):
    """One pass. Returns a dict summary. `timings` overrides approve_gap_min / submit_window_min (tests only).

    guard(cells, now): called on every fresh read (and again before an upload); it raises to stop the pass
    (the loop uses it for the lock). extra_values: cells written with this pass (the heartbeat).
    A post is recorded as POSTING (with an intent note) in the sheet BEFORE the upload is called."""
    tim = dict(cfg["timings"])
    tim.update(timings or {})
    tz = ZoneInfo(cfg.get("display_tz", "UTC"))
    snap = io.read()
    if guard:
        guard(snap.cells, now)
    items, cells = snap.items, snap.cells
    log, all_log, notes, out = list(extra_log or []), [], [], {}
    log_rows = [int(cells.get("log_rows") or 1)]
    router = Router(fake_uploader or poster.FakeUploader(), uploader, allow_real_upload)
    ticked = lambda v: v is True or str(v).strip().upper() == "TRUE"
    approve = ticked(cells.get("approve"))
    confirm = ticked(cells.get("add_confirm"))
    write_vals = dict(extra_values or {})

    def flush(values):
        n = len(log)
        io.write(now, items=items, values=values, log_lines=log, log_rows=log_rows[0])
        log_rows[0] += n
        all_log.extend(log)
        del log[:]

    # 0) settle anything an earlier run left in POSTING (crash, timeout, restart)
    poster.reconcile(items, router, now, log, notes=notes)

    # 1) Add link, only when everything is used
    if confirm:
        url = str(cells.get("add_link") or "").strip()
        if states.all_used(items) and sheet.TWITCH_URL.match(url):
            n = max(i.n for i in items) + 1
            kind = "vod" if "/videos/" in url or "/video/" in url else "clip"
            items.append(Item(n=n, source=url, kind=kind))
            log.append(("ADD_LINK", "queued #%d %s" % (n, kind)))
            out["added"] = n
        else:
            log.append(("ADD_LINK_REJECTED", "not all used yet or not a twitch link"))
        write_vals["add_link"], write_vals["add_confirm"] = "", False

    # 2) fake clips only: instant edit so the whole flow can be tested
    if fake:
        it = states.next_to_edit([i for i in items if i.kind == "fake"])
        if it:
            states.transition(it, "EDITING")
            it.moment = "0-30 (fake)"
            it.caption = capmod.build_caption(cfg, "Fake test clip number %d, nothing is posted" % it.n)
            states.transition(it, "READY")
            log.append(("FAKE_EDIT", "clip #%d READY" % it.n))

    # 3) decide
    acts = states.plan(items, now, tim, approve)
    by_n = {i.n: i for i in items}
    final = []
    for a, n in acts:
        if a == "POST" and by_n[n].kind != "fake" and not (allow_real_upload and uploader is not None):
            final.append(("CLEAR_TICK", n))
            log.append(("POST_BLOCKED", "clip #%d: uploader not enabled (waits for 'test upload now')" % n))
        else:
            final.append((a, n))
    states.apply(items, final, now)
    for a, n in final:
        if a in ("AWAIT", "DONE"):
            log.append((a, "clip #%s" % n))
        if a in ("CLEAR_TICK", "POST"):
            write_vals["approve"] = False
        if a == "CLEAR_TICK":
            log.append(("TICK_CLEARED", "APPROVE ticked while nothing was waiting for approval"))

    # 4) post: record POSTING + intent first, check we still own the sheet, only then upload
    fresh = poster.begin(items, now, run_id, {n for a, n in final if a == "POST"})
    if fresh:
        for i in fresh:
            log.append(("POSTING", "clip #%d (intent recorded before the upload)" % i.n))
        flush(write_vals)
        write_vals = {}
        snap2 = io.read()
        if guard:
            guard(snap2.cells, now)
        mine = [i for i in fresh if poster.still_mine(snap2.items, i)]
        if len(mine) != len(fresh):
            log.append(("POST_ABORTED", "another loop recorded this post first, nothing uploaded"))
            items = snap2.items  # the sheet is the truth, do not overwrite it
            fresh = []
        for i in fresh:
            if poster.execute(i, router, now, log) == "posted":
                out["posted"] = i.n

    cellvals = render_cells(items, now, tim, tz)
    write_vals.update(cellvals)
    flush(write_vals)
    out.update({"actions": final, "log": all_log, "notes": notes, "state": cellvals["state"],
                "states": {i.n: i.state for i in items}})
    return out
