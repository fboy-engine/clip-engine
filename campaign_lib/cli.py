"""CLI used by the campaign workflow.

  python -m campaign_lib.cli secrets
  python -m campaign_lib.cli probe  --sheet-id ID
  python -m campaign_lib.cli init   --sheet-id ID [--fake N]
  python -m campaign_lib.cli tick   --sheet-id ID [--fake] [--gap-min N] [--submit-min N]
  python -m campaign_lib.cli loop   --sheet-id ID --slot a|b|w [--handoff-token T] [--fake] [--gap-min N] [--submit-min N]
  python -m campaign_lib.cli watchdog --sheet-id ID

No Instagram code is reachable from here."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import config, composio, sheet
from .states import Item


def cmd_secrets(cfg, a):
    for name in cfg["secrets"].values():  # prints only set / NOT SET, never value, prefix or length
        print("%s: %s" % (name, "set" if os.environ.get(name) else "NOT SET"))
    return 0


def _io(cfg, a):
    sid = a.sheet_id or (cfg.get("sheet") or {}).get("id")
    return sheet.SheetIO(composio.Client(), sid)


def cmd_probe(cfg, a):
    status = composio.diagnose()
    if status != 200:
        print("probe STOPPED: documented request not accepted (HTTP %s); no fallback attempted" % status)
        return 1
    io = _io(cfg, a)
    snap = io.read()
    print("account: ACTIVE googlesheets (discovered), read OK")
    print("queue items:", [(i.n, i.kind, i.state) for i in snap.items])
    print("cells:", json.dumps({k: v for k, v in snap.cells.items()}, default=str)[:900])
    return 0


def cmd_init(cfg, a):
    io = _io(cfg, a)
    now = datetime.now(timezone.utc)
    items = [Item(n=i, source="FAKE-%d" % i, kind="fake") for i in range(1, a.fake + 1)] if a.fake > 0 else None
    data = sheet.init_payload(cfg, now, items)
    io.c.execute("GOOGLESHEETS_UPDATE_VALUES_BATCH",
                 {"spreadsheet_id": io.id, "valueInputOption": "RAW", "data": data})
    reach = sheet.reach_payload(cfg)
    if reach:  # formula cell: USER_ENTERED, separate call
        io.c.execute("GOOGLESHEETS_UPDATE_VALUES_BATCH",
                     {"spreadsheet_id": io.id, "valueInputOption": "USER_ENTERED", "data": reach})
    print("init OK: %d ranges, %d queue rows" % (len(data), len(data[1]["values"]) - 1))
    return 0


def cmd_tick(cfg, a):
    from . import runner
    io = _io(cfg, a)
    t = {}
    if a.gap_min is not None:
        t["approve_gap_min"] = a.gap_min
    if a.submit_min is not None:
        t["submit_window_min"] = a.submit_min
    res = runner.tick(io, cfg, datetime.now(timezone.utc), fake=a.fake, timings=t)
    print("tick OK:", json.dumps(res, default=str))
    return 0


def cmd_loop(cfg, a):
    from . import service
    t = {}
    if a.gap_min is not None:
        t["approve_gap_min"] = a.gap_min
    if a.submit_min is not None:
        t["submit_window_min"] = a.submit_min
    return service.run_loop(cfg, _io(cfg, a), service.new_github(), a.slot, a.handoff_token, fake=a.fake, timings=t)


def cmd_watchdog(cfg, a):
    from . import service
    return service.run_watchdog(cfg, _io(cfg, a), service.new_github())


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["secrets", "probe", "init", "tick", "loop", "watchdog"])
    p.add_argument("--slot", default="w", choices=["a", "b", "w"])
    p.add_argument("--handoff-token", default="")
    p.add_argument("--campaign", default="lauren-hamden")
    p.add_argument("--sheet-id", default="")
    p.add_argument("--fake", nargs="?", const=-1, type=int, default=0)
    p.add_argument("--gap-min", type=float, default=None)
    p.add_argument("--submit-min", type=float, default=None)
    a = p.parse_args(argv)
    cfg = config.load(a.campaign)
    if a.cmd in ("tick", "loop"):
        a.fake = a.fake != 0
    try:
        return {"secrets": cmd_secrets, "probe": cmd_probe, "init": cmd_init, "tick": cmd_tick,
                "loop": cmd_loop, "watchdog": cmd_watchdog}[a.cmd](cfg, a)
    except (composio.ComposioError, ValueError) as e:
        print("ERROR:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
