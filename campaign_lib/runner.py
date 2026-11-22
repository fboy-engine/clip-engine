"""One short pass of the campaign loop: read the sheet, decide (states.plan), write the sheet.

B3 scope: NO Instagram code exists here. Only items of kind "fake" are ever moved to POSTING/POSTED
(with a clearly fake permalink). A real item that gets an APPROVE tick is left alone, the tick is
cleared and the log says the uploader is not enabled."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import caption as capmod
from . import sheet, states
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


def tick(io, cfg, now, fake=False, timings=None):
    """Returns a dict summary. `timings` overrides approve_gap_min / submit_window_min (tests only)."""
    tim = dict(cfg["timings"])
    tim.update(timings or {})
    tz = ZoneInfo(cfg.get("display_tz", "UTC"))
    snap = io.read()
    items, cells = snap.items, snap.cells
    log, out = [], {}
    ticked = lambda v: v is True or str(v).strip().upper() == "TRUE"
    approve = ticked(cells.get("approve"))
    confirm = ticked(cells.get("add_confirm"))
    write_vals = {}

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
        if a == "POST" and by_n[n].kind != "fake":
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

    # 4) fake upload: no Instagram anywhere; clearly fake id and link
    for i in items:
        if i.state == "POSTING" and i.kind == "fake":
            states.transition(i, "POSTED", now=now, media_id="FAKE-%d" % i.n,
                              permalink="https://www.instagram.com/reel/FAKE%d/" % i.n)
            log.append(("FAKE_POSTED", "clip #%d (no Instagram call)" % i.n))
            out["posted"] = i.n

    cellvals = render_cells(items, now, tim, tz)
    write_vals.update(cellvals)
    io.write(now, items=items, values=write_vals, log_lines=log, log_rows=cells.get("log_rows"))
    out.update({"actions": final, "log": log, "state": cellvals["state"], "states": {i.n: i.state for i in items}})
    return out
