"""Engine -> panel. The loop never renders: it dispatches clip.yml (the render workflow, unchanged), polls
the run, and reads its artifact.

  QUEUED  -> EDITING   dispatch clip.yml for the next link (item.run = PENDING@<utc> until the run id is found)
  EDITING -> READY     run succeeded AND grade.json has no failed check: caption filled in
  EDITING -> QUEUED    run failed / a gate failed: NEEDS_FIX in the Note, re-cut with a different window (MAX_FIX_TRIES)
  EDITING -> SKIPPED   only after MAX_FIX_TRIES failures, or a source that cannot be used / never finished
  PAUSE (campaign.json "pause": true): nothing new is rendered and nothing is skipped

One clip is edited at a time (states.next_to_edit). A GitHub error never crashes the loop: it is logged and
the next tick tries again. Vod sources are handled by vod.py."""
import json
import os
from datetime import datetime, timedelta, timezone

from . import caption as capmod
from . import sheet, states
from .ghclient import GitHubError

CLIP_WORKFLOW = "clip.yml"
PENDING = "PENDING@"
FIND_BACK_S = 60          # a run created this long before our dispatch time may still be ours (clock skew)
LOST_AFTER_S = 600        # dispatched but no run appeared: try again
MAX_ATTEMPTS = 3          # dispatch / probe attempts per clip before it is skipped
MAX_FIX_TRIES = MAX_ATTEMPTS   # a clip that fails a gate is re-cut with a different window this many times, only then SKIPPED
RETRY_SHIFT_S = 4.0       # each retry starts this much later in the source (only while 25 s or more remain)
MIN_CLIP_S = 25.0
MAX_WAIT_S = 3 * 3600     # a clip run still not finished after this long: skip it
FALLBACK_LINE = "Lauren Hamden stream moment"
BOT = "github-actions[bot]"


def clip_window(duration, rcfg=None, shift=0.0):
    """(render_start, render_end) in the downloaded clip. The whole short clip, minus a 2 s lead, at most
    max_span_s (the cam upscaler time budget). Raises ValueError for a source too short to use."""
    r = rcfg or {}
    start, span = float(r.get("start_s", 2)), float(r.get("max_span_s", 27))
    if duration < start + 10:
        start = 0.0
    if shift and duration - (start + shift) >= MIN_CLIP_S:      # a retry looks at a different part of the source
        start += shift
    end = min(float(duration), start + span)
    if end - start < 8:
        raise ValueError("source is only %.0f s long" % duration)
    return round(start, 1), round(end, 1)


def source_cfg(cfg, item):
    for s in cfg.get("sources", []):
        if s.get("n") == item.n or s.get("url") == item.source:
            return s
    return {}


def dispatch_inputs(cfg, item, start, end):
    inputs = {"url": item.source, "campaign": "lauren-hamden", "dry_run": "true", "warp": "false", "cookies": "false",
              "render": "true", "render_start": "%g" % start, "render_end": "%g" % end, "cache_only": "false"}
    rect = source_cfg(cfg, item).get("cam_rect")
    if rect:
        inputs["cam_rect"] = rect
    return inputs


def judge(grade, names):
    """(ok, reason). Pass = clip.mp4 exists and grade.json has no failed check (warn is not a failure)."""
    if not any(n.rsplit("/", 1)[-1] == "clip.mp4" for n in names):
        return False, "no clip.mp4 in the run artifact"
    if not grade or "checks" not in grade:
        return False, "no grade.json in the run artifact"
    bad = [c for c in grade["checks"] if c.get("status") == "fail"]
    if bad:
        return False, ("gates failed: " + "; ".join("%s (%s)" % (c.get("name"), str(c.get("detail", ""))[:60]) for c in bad))[:240]
    return True, ""


def caption_for(cfg, render):
    """(hook text, final caption). The hook is the one short English line; a bad one falls back, never ships."""
    hook = " ".join(str(((render or {}).get("hook") or {}).get("text") or "").split())
    line = hook.lower().capitalize() if hook else ""
    for cand in (line, FALLBACK_LINE):
        try:
            return hook, capmod.build_caption(cfg, cand)
        except capmod.CaptionError:
            continue
    raise capmod.CaptionError("no usable caption line")


def _read(zf, base):
    for n in zf.namelist():
        if n.rsplit("/", 1)[-1] == base:
            try:
                return json.loads(zf.read(n).decode("utf-8"))
            except ValueError:
                return None
    return None


def fetch_clip(gh, item, dest_dir):
    """Download the rendered clip of item.run into dest_dir and set item.file. Raises GitHubError."""
    if not item.run or item.run.startswith(PENDING):
        raise GitHubError("clip #%d has no finished run" % item.n)
    zf = gh.artifact_zip(item.run, "clip-")
    if zf is None:
        raise GitHubError("run artifact is gone")
    for n in zf.namelist():
        if n.rsplit("/", 1)[-1] == "clip.mp4":
            os.makedirs(dest_dir, exist_ok=True)
            path = os.path.join(dest_dir, "clip-%d.mp4" % item.n)
            with open(path, "wb") as f:
                f.write(zf.read(n))
            item.file = path
            return path
    raise GitHubError("clip.mp4 is not in the run artifact")


class Editor:
    def __init__(self, gh, cfg, probe, vod=None):
        self.gh, self.cfg, self.probe, self.vod = gh, cfg, probe, vod

    def advance(self, items, now, log):
        try:
            for it in [i for i in items if i.state == "EDITING"]:
                self._poll(it, items, now, log)
            if self.cfg.get("pause"):   # PAUSE (campaign.json): no new render, nothing is skipped, until part A passes a clip
                return
            if not any(i.state == "EDITING" for i in items):
                nxt = states.next_to_edit(items)
                if nxt is not None:
                    self._start(nxt, now, log)
        except GitHubError as e:
            log.append(("EDITOR_ERROR", str(e)[:160]))

    # ---- helpers
    @staticmethod
    def _attempts(it):
        try:
            return int(it.note.split("attempt ")[1].split()[0]) if "attempt " in it.note else 0
        except (ValueError, IndexError):
            return 0

    def _skip(self, it, reason, log):
        states.transition(it, "SKIPPED")
        it.note = ("skipped: " + reason)[:240]
        log.append(("SKIPPED", "clip #%d: %s" % (it.n, reason[:120])))

    def _fail(self, it, reason, log):
        """A gate failed or the run failed. Never silently consumed: parked as NEEDS_FIX (back in the queue, reason in the
        note) and re-cut with a different window; SKIPPED only after MAX_FIX_TRIES. While PAUSE is on it is only parked."""
        n = self._attempts(it)
        paused = bool(self.cfg.get("pause"))
        if it.kind == "vod" and not paused:       # a vod has its own window logic (vod.py): a failed render keeps its old path
            return self._skip(it, reason, log)
        if paused or n < MAX_FIX_TRIES:
            states.transition(it, "QUEUED")
            it.run = ""
            it.note = ("attempt %d NEEDS_FIX%s: %s" % (n, " (paused)" if paused else "", reason))[:240]
            log.append(("NEEDS_FIX", "clip #%d: %s" % (it.n, reason[:120])))
            return
        self._skip(it, reason, log)

    # ---- start
    def _start(self, it, now, log):
        if it.kind == "vod":
            if self.vod is not None:
                self.vod.start(it, now, log)
            return
        n = self._attempts(it)
        try:
            dur = float(self.probe(it.source)["duration"])
            start, end = clip_window(dur, self.cfg.get("render"), shift=RETRY_SHIFT_S * n)
        except Exception as e:  # unreachable link, deleted clip, too short ...
            if isinstance(e, ValueError) or n + 1 >= MAX_ATTEMPTS:
                return self._skip(it, "source not usable (%s)" % type(e).__name__, log)
            it.note = "attempt %d (probe failed)" % (n + 1)
            return
        states.transition(it, "EDITING")
        it.moment, it.run, it.note = "%g-%g" % (start, end), PENDING + sheet.iso(now), "attempt %d" % (n + 1)
        try:
            self.gh.dispatch(CLIP_WORKFLOW, dispatch_inputs(self.cfg, it, start, end))
        except GitHubError as e:
            states.transition(it, "QUEUED")
            it.run, it.note = "", "attempt %d (dispatch failed)" % (n + 1)
            if n + 1 >= MAX_ATTEMPTS:
                return self._skip(it, "could not start the render: %s" % e, log)
            raise
        log.append(("EDIT_START", "clip #%d dispatched (%s s)" % (it.n, it.moment)))

    # ---- poll
    def _poll(self, it, items, now, log):
        if not it.run:
            states.transition(it, "QUEUED")
            return
        if it.run.startswith(PENDING):
            return self._find_run(it, items, now, log)
        run = self.gh.get_run(it.run)
        if run.get("status") != "completed":
            created = sheet.parse_iso(str(run.get("created_at", "")).replace("+00:00", "Z"))
            if created and (now - created).total_seconds() > MAX_WAIT_S:
                self._skip(it, "render run did not finish in %d h" % (MAX_WAIT_S // 3600), log)
            return
        if it.kind == "vod" and self.vod is not None and self.vod.is_find(it):
            return self.vod.after_find(it, run, now, log)  # a find run has no clip: vod.py picks the moment and renders
        zf = self.gh.artifact_zip(it.run, "clip-")
        grade = _read(zf, "grade.json") if zf else None
        render = _read(zf, "render.json") if zf else None
        names = zf.namelist() if zf else []
        if run.get("conclusion") != "success":
            step = self.gh.failed_step(it.run)
            why = "render run %s%s" % (run.get("conclusion"), " at step '%s'" % step if step else "")
            cam = _read(zf, "cam_find.json") if zf else None
            if cam and cam.get("reasons"):
                why += " | cam: %s" % str(cam["reasons"][0])[:100]
            return self._fail(it, why, log)
        ok, why = judge(grade, names)
        if not ok:
            return self._fail(it, why, log)
        try:
            hook, cap = caption_for(self.cfg, render)
        except capmod.CaptionError as e:
            return self._fail(it, "caption: %s" % e, log)
        it.hook, it.caption, it.note = hook, cap, ""
        states.transition(it, "READY")
        log.append(("READY", "clip #%d passed every gate" % it.n))

    def _find_run(self, it, items, now, log):
        t = sheet.parse_iso(it.run[len(PENDING):]) or now
        since = sheet.iso(t - timedelta(seconds=FIND_BACK_S))
        known = {i.run for i in items if i.run and not i.run.startswith(PENDING)}
        cands = [r for r in self.gh.list_runs(CLIP_WORKFLOW, since) if str(r.get("id")) not in known]
        cands.sort(key=lambda r: (((r.get("triggering_actor") or r.get("actor") or {}).get("login") != BOT),
                                  r.get("created_at", "")))
        if cands:
            it.run = str(cands[0]["id"])
            log.append(("EDIT_RUN", "clip #%d run id recorded" % it.n))
        elif (now - t).total_seconds() > LOST_AFTER_S:
            n = self._attempts(it)
            if n >= MAX_ATTEMPTS:
                return self._skip(it, "the render run never appeared", log)
            states.transition(it, "QUEUED")
            it.run, it.note = "", "attempt %d (run not found)" % n
            log.append(("DISPATCH_LOST", "clip #%d will be dispatched again" % it.n))
