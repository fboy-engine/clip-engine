"""Source finder for a stream (VOD) link. Campaign-side only: the clip engine (clip.yml) is not changed.

A VOD item becomes ONE clip in two clip.yml runs, both driven by the loop (it never renders):
  1. find run   : a window of a "Grand Theft Auto VI" chapter only (other chapters are never downloaded);
                  the engine ranks candidate moments (artifact find-<run>/candidates.json)
  2. render run : the best candidate that passed the engine's own gates, at most max_span_s long;
                  the engine's cam check and grader decide READY or SKIPPED (editor.py reads that run as for a clip)

Note field of the item (sheet column Note): vod:find:<k> | vod:render:<k> | vod:next:<k>, k = window index.
A window with no usable moment moves on to the next window; when all (at most MAX_WINDOWS) are spent the item is
SKIPPED and the queue moves on (a skipped item never blocks)."""
import json
import re

from . import sheet, states
from .editor import CLIP_WORKFLOW, MAX_ATTEMPTS, PENDING, source_cfg
from .ghclient import GitHubError

GAME = "grand theft auto vi"   # chapter title, compared case-insensitively with spaces collapsed
FIND_SPAN_S = 600              # one find run looks at most this far (seconds of the stream)
MIN_WINDOW_S = 120             # a chapter piece shorter than this is not worth a run
MAX_WINDOWS = 6
MIN_RENDER_S = 8
_NOTE = re.compile(r"vod:(find|render|next):(\d+)")


def norm(title):
    return " ".join(str(title or "").lower().split())


def gta_windows(chapters, span=FIND_SPAN_S, min_len=MIN_WINDOW_S):
    """[(start, end)] seconds of the stream, in time order, ONLY inside chapters titled "Grand Theft Auto VI"."""
    out = []
    for ch in sorted((c for c in chapters or [] if norm(c.get("title")) == GAME), key=lambda c: float(c["start_time"])):
        t, end = float(ch["start_time"]), float(ch["end_time"])
        while end - t >= min_len:
            out.append((t, min(end, t + span)))
            t += span
    return out


def best_candidate(data, max_span, min_len=MIN_RENDER_S):
    """(render_start, render_end, stream_start) in WINDOW-FILE seconds (+ absolute start), or None.
    candidates.json is sorted best first; only a candidate that passed the engine's gates counts."""
    for c in (data or {}).get("candidates", []):
        if not c.get("gate_ok"):
            continue
        s, e = float(c["start"]), min(float(c["end"]), float(c["start"]) + max_span)
        if e - s >= min_len:
            return round(s, 1), round(e, 1), float(c.get("stream_start", s))
    return None


def _read_json(zf, base):
    for n in (zf.namelist() if zf else []):
        if n.rsplit("/", 1)[-1] == base:
            try:
                return json.loads(zf.read(n).decode("utf-8"))
            except ValueError:
                return None
    return None


def parse_note(note):
    """(phase, k) from the Note, ("", 0) when it holds none of ours."""
    m = _NOTE.search(note or "")
    return (m.group(1), int(m.group(2))) if m else ("", 0)


def attempts(note):
    try:
        return int(note.split("attempt ")[1].split()[0]) if "attempt " in (note or "") else 0
    except (ValueError, IndexError):
        return 0


class Vod:
    def __init__(self, gh, cfg, chapters_fn):
        """chapters_fn(url) -> [{"title", "start_time", "end_time"}]; raises ValueError when the source can never work."""
        self.gh, self.cfg, self.chapters = gh, cfg, chapters_fn

    # ---- helpers
    def _skip(self, it, reason, log):
        states.transition(it, "SKIPPED")
        it.note = ("skipped: " + reason)[:240]
        log.append(("SKIPPED", "clip #%d: %s" % (it.n, reason[:120])))

    def _inputs(self, it, start, end):
        d = {"url": it.source, "campaign": "lauren-hamden", "dry_run": "true", "warp": "false", "cookies": "false",
             "start_sec": "%g" % start, "end_sec": "%g" % end, "cache_only": "false"}
        rect = source_cfg(self.cfg, it).get("cam_rect")
        if rect:
            d["cam_rect"] = rect
        return d

    def is_find(self, it):
        return parse_note(it.note)[0] == "find"

    def _windows(self, it):
        return gta_windows(self.chapters(it.source))[:MAX_WINDOWS]

    # ---- step 1: find run for window k
    def start(self, it, now, log):
        phase, k = parse_note(it.note)
        n = attempts(it.note)
        try:
            wins = self._windows(it)
        except ValueError as e:
            return self._skip(it, "stream not usable (%s)" % e, log)
        except Exception as e:  # network trouble: try again on a later tick, a few times
            if n + 1 >= MAX_ATTEMPTS:
                return self._skip(it, "chapters not readable (%s)" % type(e).__name__, log)
            it.note = "vod:next:%d attempt %d (chapters failed)" % (k, n + 1)
            return
        if not wins:
            return self._skip(it, 'no "Grand Theft Auto VI" chapter in this stream', log)
        if k >= len(wins):
            return self._skip(it, "no usable moment in the Grand Theft Auto VI windows (tried %d)" % len(wins), log)
        s, e = wins[k]
        inp = self._inputs(it, s, e)
        inp.update({"find": "true", "render": "false", "find_top": "8"})
        states.transition(it, "EDITING")
        it.moment, it.run, it.note = "%g-%g" % (s, e), PENDING + sheet.iso(now), "vod:find:%d attempt %d" % (k, n + 1)
        try:
            self.gh.dispatch(CLIP_WORKFLOW, inp)
        except GitHubError as err:
            states.transition(it, "QUEUED")
            it.run, it.note = "", "vod:next:%d attempt %d (dispatch failed)" % (k, n + 1)
            if n + 1 >= MAX_ATTEMPTS:
                return self._skip(it, "could not start the find run: %s" % err, log)
            raise
        log.append(("VOD_FIND", "clip #%d: window %d of %d dispatched (%s s)" % (it.n, k + 1, len(wins), it.moment)))

    # ---- step 2: the find run finished
    def after_find(self, it, run, now, log):
        _, k = parse_note(it.note)
        if run.get("conclusion") != "success":
            return self._next(it, k, "find run %s" % run.get("conclusion"), log)
        data = _read_json(self.gh.artifact_zip(it.run, "find-"), "candidates.json")
        span = float((self.cfg.get("render") or {}).get("max_span_s", 27))
        pick = best_candidate(data, span)
        if pick is None:
            return self._next(it, k, "no candidate passed the engine's gates", log)
        rs, re_, abs_start = pick
        s, e = [float(x) for x in it.moment.split("-")]
        inp = self._inputs(it, s, e)
        inp.update({"find": "false", "render": "true", "render_start": "%g" % rs, "render_end": "%g" % re_})
        it.moment, it.run, it.note = "%g-%g" % (round(abs_start, 1), round(abs_start + (re_ - rs), 1)), \
            PENDING + sheet.iso(now), "vod:render:%d attempt %d" % (k, attempts(it.note))
        try:
            self.gh.dispatch(CLIP_WORKFLOW, inp)
        except GitHubError as err:
            it.run, it.note = "", "vod:next:%d attempt %d (render dispatch failed)" % (k, attempts(it.note))
            states.transition(it, "QUEUED")
            raise err
        log.append(("VOD_RENDER", "clip #%d: best moment dispatched (%s s of the stream)" % (it.n, it.moment)))

    def _next(self, it, k, why, log):
        """Nothing usable in window k: the next window on the next tick (the item goes back to QUEUED)."""
        states.transition(it, "QUEUED")
        it.run, it.note = "", "vod:next:%d (%s)" % (k + 1, why[:60])
        log.append(("VOD_NEXT", "clip #%d: window %d gave nothing (%s)" % (it.n, k + 1, why[:60])))
