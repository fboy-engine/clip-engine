"""Runtime for the two long-lived workflow modes: `loop` (the self-chaining engine) and `watchdog` (cron).

The loop never renders: it dispatches clip.yml through editor.py. Everything printed goes through safelog.
Nothing here uploads by itself: the Instagram uploader is only built in dry-run form and runner.Router lets a
real post through only when the sheet's single-use switch names that clip ("test upload now")."""
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone

from . import composio, editor, engine, instagram, loop, runner, safelog, sheet, vod
from .ghclient import GitHub


def probe_source(url):
    """editor.Editor probe: {"duration": seconds}. ValueError = this source can never work (skip it);
    any other exception = try again later."""
    p = subprocess.run(["yt-dlp", "--no-colors", "--no-playlist", "--skip-download", "-J", url],
                       capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        raise RuntimeError("probe failed")
    j = json.loads(p.stdout)
    if j.get("is_live"):
        raise ValueError("source is live")
    dur = j.get("duration")
    if not dur:
        raise ValueError("source has no duration")
    return {"duration": float(dur)}


def chapters_source(url):
    """vod.Vod chapters: [{"title", "start_time", "end_time"}] of a stream (metadata only, nothing is downloaded).
    ValueError = the stream can never work (live, unreadable JSON); other errors = try again later."""
    p = subprocess.run(["yt-dlp", "--no-colors", "--no-playlist", "--skip-download", "-J", url],
                       capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        raise RuntimeError("probe failed")
    try:
        j = json.loads(p.stdout)
    except ValueError:
        raise ValueError("unreadable stream info")
    if j.get("is_live"):
        raise ValueError("stream is live")
    return [{"title": c.get("title"), "start_time": c.get("start_time"), "end_time": c.get("end_time")}
            for c in (j.get("chapters") or []) if c.get("start_time") is not None and c.get("end_time") is not None]


def run_id_from_env(environ=None):
    e = os.environ if environ is None else environ
    return "%s-%s" % (e.get("GITHUB_RUN_ID") or "local", e.get("GITHUB_RUN_ATTEMPT") or "1")


def make_logger(cfg, environ=None):
    e = os.environ if environ is None else environ
    names = list(cfg["secrets"].values()) + ["GITHUB_TOKEN"]
    return safelog.Logger(safelog.secret_values(names, e))


def build_uploader(cfg, gh, environ=None):
    """Dry-run uploader (real=False). The clip file is fetched from the render artifact only when it is needed."""
    e = os.environ if environ is None else environ
    dest = os.path.join(e.get("RUNNER_TEMP") or tempfile.gettempdir(), "campaign-clips")
    return instagram.InstagramUploader(
        e.get(cfg["secrets"]["instagram_token"], ""), e.get(cfg["secrets"]["instagram_user_id"], ""),
        real=False, media=lambda item: editor.fetch_clip(gh, item, dest))


def run_loop(cfg, io, gh, slot, token, fake=False, timings=None, now_fn=None, sleep_fn=None, log=None, environ=None):
    """One loop run. Returns the process exit code."""
    import time
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    sleep_fn = sleep_fn or time.sleep
    log = log or make_logger(cfg, environ)
    my_id = run_id_from_env(environ)
    ed = None if fake else editor.Editor(gh, cfg, probe_source, vod=vod.Vod(gh, cfg, chapters_source))
    up = build_uploader(cfg, gh, environ)
    seen = {"states": None}

    def tick(now, guard, heartbeat):
        res = runner.tick(io, cfg, now, fake=fake, timings=timings, guard=guard, extra_values=heartbeat,
                          uploader=up, run_id=my_id, editor=ed)
        events = [e for e, _ in res.get("log", [])]
        if events or res.get("states") != seen["states"]:  # quiet ticks print nothing; no captions, no links
            log("TICK", events=",".join(events) or "-", states=json.dumps(res.get("states"), sort_keys=True))
            seen["states"] = res.get("states")
        return res

    return loop.run(io, cfg, my_id, slot or "w", token or "", tick, gh, now_fn, sleep_fn, log)


def run_watchdog(cfg, io, gh, now=None, log=None, environ=None):
    """Cron entry. Starts a loop ONLY when no loop is alive and nobody typed STOP. Returns 0 always (a failed
    dispatch is logged; the next cron tick tries again)."""
    now = now or datetime.now(timezone.utc)
    log = log or make_logger(cfg, environ)
    cells = io.read().cells
    if not engine.watchdog_should_start(cells, now):
        log("WATCHDOG", action="none", reason="a loop is alive or STOP is set")
        return 0
    try:
        gh.dispatch(loop.LOOP_WORKFLOW, {"mode": "loop", "slot": "w", "handoff_token": ""})
        log("WATCHDOG", action="started a loop")
    except Exception as e:
        log("WATCHDOG", action="dispatch failed", err=type(e).__name__)
    return 0


def new_github():
    return GitHub()


def new_io(cfg, sheet_id=""):
    return sheet.SheetIO(composio.Client(), sheet_id or (cfg.get("sheet") or {}).get("id"))
