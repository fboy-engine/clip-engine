"""Sheet layout + read/write for the campaign sheet "Clip Engine - Lauren Hamden GTA6".

The sheet is the single source of truth. Tabs: Campaign (box), Queue, Log.
Everything is written with RAW so times and links stay plain text."""
import re
from datetime import datetime, timezone

from . import composio
from .states import Item

CAMPAIGN, QUEUE, LOG = "Campaign", "Queue", "Log"

# Campaign tab: labels in column A, values in column B.
CELLS = {
    "name": 3, "state": 4, "next": 5, "caption": 6, "approve": 7, "timer": 8, "opens": 9,
    "link": 10, "submit": 11, "add_link": 12, "add_confirm": 13, "updated": 14,
    "unlock_utc": 16, "log_rows": 17,
    # engine cells (rows 18-22): written by the loop, read by the panel. Never typed by hand,
    # except lock_run = STOP, the kill switch (no loop starts and every loop exits).
    "lock_run": 18, "heartbeat": 19, "ig_expires": 20, "handoff": 21, "loop_start": 22,
    "real_upload": 23,  # the ONE switch: "ONE:<clip n>" = post that clip for real, once; empty = dry-run only
}
LABELS = [
    (3, "Campaign"), (4, "State"), (5, "Next clip"), (6, "Caption"), (7, "APPROVE (tick to post)"),
    (8, "Timer"), (9, "APPROVE opens at"), (10, "Copy link (long-press)"), (11, "Reach submit"),
    (12, "Add link (paste stream link)"), (13, "Confirm add link (tick)"), (14, "Last update"),
    (16, "machine: approve unlocks (UTC)"), (17, "machine: log rows used"),
    (18, "engine: loop run id (type STOP = stop)"), (19, "engine: heartbeat (UTC)"),
    (20, "engine: Instagram token expires"), (21, "engine: handoff offer"), (22, "engine: loop started (UTC)"),
    (23, "engine: real upload switch (ONE:<clip #> = post that one clip for real)"),
]
QUEUE_HEAD = ["#", "Source", "Kind", "State", "Moment", "Hook", "Caption", "Note", "Posted at (UTC)",
              "Media id", "Reel link", "Submit before", "Updated", "Clip run"]
LOG_HEAD = ["Time (UTC)", "Event", "Detail"]
QUEUE_LAST_ROW = 100
CAMPAIGN_LAST_ROW = 25  # read window of the Campaign tab (cells end at row 22)
TWITCH_URL = re.compile(r"^https://(www\.|m\.)?twitch\.tv/(videos/\d+|[A-Za-z0-9_]+/clip/[A-Za-z0-9_-]+|[A-Za-z0-9_]+/video/\d+)/?(\?.*)?$")


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def parse_iso(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def item_row(i, now):
    return [i.n, i.source, i.kind, i.state, i.moment, i.hook, i.caption, i.note, iso(i.posted_at),
            i.media_id, i.permalink, "", iso(now), i.run]


def row_item(row):
    row = list(row) + [""] * (14 - len(row))
    if row[0] in ("", None):
        return None
    s = lambda v: "" if v is None else str(v)
    return Item(n=int(row[0]), source=s(row[1]), kind=s(row[2]) or "clip", state=s(row[3]) or "QUEUED",
                moment=s(row[4]), hook=s(row[5]), caption=s(row[6]), note=s(row[7]),
                posted_at=parse_iso(row[8]), media_id=s(row[9]), permalink=s(row[10]), run=s(row[13]))


class Snapshot:
    def __init__(self, items, cells):
        self.items, self.cells = items, cells


class SheetIO:
    def __init__(self, client, sheet_id):
        if not sheet_id:
            raise ValueError("sheet id is empty (campaign.json sheet.id or workflow input sheet_id)")
        self.c, self.id = client, sheet_id

    def read(self):
        data = self.c.execute("GOOGLESHEETS_BATCH_GET", {
            "spreadsheet_id": self.id, "valueRenderOption": "UNFORMATTED_VALUE",
            "ranges": ["%s!A1:B%d" % (CAMPAIGN, CAMPAIGN_LAST_ROW), "%s!A1:N%d" % (QUEUE, QUEUE_LAST_ROW)]})
        vr = composio.find_key(data, "valueRanges")
        if not vr or len(vr) < 2:
            raise composio.ComposioError("unexpected BATCH_GET answer (no valueRanges)")
        camp = vr[0].get("values") or []
        cells = {}
        for name, r in CELLS.items():
            row = camp[r - 1] if r - 1 < len(camp) else []
            cells[name] = row[1] if len(row) > 1 else ""
        items = []
        for row in (vr[1].get("values") or [])[1:]:
            it = row_item(row)
            if it:
                items.append(it)
        return Snapshot(items, cells)

    def write(self, now, items=None, values=None, log_lines=None, log_rows=None):
        """values: {cell_name: value}. Each named cell is its own range, so a cell the user edits
        between our read and write (APPROVE, Add link) is only overwritten when we mean to."""
        data = []
        for name, v in (values or {}).items():
            data.append({"range": "%s!B%d" % (CAMPAIGN, CELLS[name]), "values": [[v]]})
        if items is not None:
            rows = [item_row(i, now) for i in sorted(items, key=lambda x: x.n)]
            if rows:
                data.append({"range": "%s!A2:N%d" % (QUEUE, 1 + len(rows)), "values": rows})
        if log_lines:
            start = int(log_rows or 1) + 1
            data.append({"range": "%s!A%d:C%d" % (LOG, start, start + len(log_lines) - 1),
                         "values": [[iso(now), a, b] for a, b in log_lines]})
            data.append({"range": "%s!B%d" % (CAMPAIGN, CELLS["log_rows"]), "values": [[start - 1 + len(log_lines)]]})
        if data:
            self.c.execute("GOOGLESHEETS_UPDATE_VALUES_BATCH",
                           {"spreadsheet_id": self.id, "valueInputOption": "RAW", "data": data})
        return len(data)


REACH_CELL = "C10"  # next to the Copy link cell (B10). Only opens the Reach page; nothing is ever submitted.


def reach_payload(cfg):
    """Tappable "Open Reach submit page" cell. A formula, so it must be written USER_ENTERED
    (the rest of the sheet is RAW). The URL is the single config value campaign.json reach_url."""
    url = cfg.get("reach_url")
    if not url:
        return []
    return [{"range": "%s!%s" % (CAMPAIGN, REACH_CELL),
             "values": [['=HYPERLINK("%s","Open Reach submit page")' % url]]}]


def init_payload(cfg, now, items=None):
    """Initial content for a fresh campaign sheet: the real one (items = the campaign sources, all
    QUEUED) or a throwaway test sheet (items = fake clips). Returns UPDATE_VALUES_BATCH `data`."""
    if items is None:
        items = [Item(n=s["n"], source=s["url"], kind=s["kind"]) for s in cfg["sources"]]
    camp = [[""] * 2 for _ in range(23)]
    camp[0] = [cfg["sheet"]["title"], ""]
    for r, label in LABELS:
        camp[r - 1][0] = label
    camp[CELLS["name"] - 1][1] = cfg["name"]
    camp[CELLS["state"] - 1][1] = "STARTING"
    camp[CELLS["approve"] - 1][1] = False
    camp[CELLS["add_confirm"] - 1][1] = False
    camp[CELLS["log_rows"] - 1][1] = 1
    rows = [item_row(i, now) for i in sorted(items, key=lambda x: x.n)]
    return [
        {"range": "%s!A1:B23" % CAMPAIGN, "values": camp},
        {"range": "%s!A1:N%d" % (QUEUE, 1 + len(rows)), "values": [QUEUE_HEAD] + rows},
        {"range": "%s!A1:C1" % LOG, "values": [LOG_HEAD]},
    ]
