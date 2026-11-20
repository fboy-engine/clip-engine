"""Find the best moments in a window, honestly.

signals (voice, audio peaks, silence gaps, screen motion) -> top windows ->
cheap ASR (tiny/small) on those windows only -> local payoff gate ->
Gemini text score -> Gemini 'watching' gate on frames of the top few -> report.
Every Gemini stage is optional: if it is unavailable the report says so and the
local score stands. Nothing here renders or posts.
"""
import base64
import json
import re
import subprocess

from . import analyze, gemini, rank_local

SR = 16000
W_LOCAL, W_GEMINI, W_WATCH = 0.3, 0.4, 0.3
SCORE_MAX = 8.5     # rank_local.score ceiling, used to put it on a 0-10 scale


def parse_json(text):
    """First JSON array/object in a model reply (it may wrap it in prose or fences)."""
    m = re.search(r"(\[.*\]|\{.*\})", text or "", re.S)
    if not m:
        raise ValueError("no JSON in reply")
    return json.loads(m.group(1))


def _num(x, lo=0.0, hi=10.0):
    return max(lo, min(hi, float(x)))


def score_prompt(cands):
    rows = []
    for c in cands:
        rows.append(f'id={c["id"]} len={c["end"] - c["start"]:.0f}s speech={c["speech_pct"]}% '
                    f'audio_peak=+{c["audio_lift_db"]}dB motion={c["motion_sd"]}sd '
                    f'text="{c["text"][:500]}"')
    return ("You rate moments from a GTA stream (gameplay + streamer) for a vertical short clip. "
            "Candidates come from audio/motion/voice signals; text is ASR and may contain errors "
            "(empty text = nobody spoke, judge by the signals). Score each 0-10: self-contained, a "
            "clear payoff inside the window, a strong first second, funny/exciting/surprising. "
            "Penalize rambling, waiting, menus. Reply with ONLY a JSON array: "
            '[{"id":0,"score":7,"reason":"max 15 words","hook":"max 6 word caption or empty"}]\n'
            + "\n".join(rows))


def watch_prompt(n):
    return (f"These {n} frames are evenly spaced across a ~35 second clip of a GTA stream. Judge "
            "only what is visible: is something actually happening (action, crash, chase, fight, "
            "funny moment) rather than a menu, loading screen, idle walking or a static scene? "
            "Reply with ONLY JSON: "
            '{"watch_score":0-10,"what":"max 20 words on what visibly happens",'
            '"facecam":"none|top-left|top-right|bottom-left|bottom-right|other",'
            '"problems":"max 15 words or none"}')


def gemini_scores(cands, gen=gemini.generate):
    """{id: {"score", "reason", "hook"}}; raises GeminiUnavailable / ValueError."""
    reply = gen(score_prompt(cands), max_chars=9000, timeout=30)
    out = {}
    for row in parse_json(reply):
        out[int(row["id"])] = {"score": _num(row["score"]), "reason": str(row.get("reason", ""))[:160],
                               "hook": str(row.get("hook", ""))[:60]}
    return out


def gemini_watch(frames, gen=gemini.generate):
    reply = gen(watch_prompt(len(frames)), images=frames, timeout=45)
    d = parse_json(reply)
    return {"score": _num(d["watch_score"]), "what": str(d.get("what", ""))[:200],
            "facecam": str(d.get("facecam", "?"))[:20], "problems": str(d.get("problems", ""))[:160]}


def frame_cmd(media, t, width=480):
    return ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.2f}", "-i", media, "-frames:v", "1",
            "-vf", f"scale={width}:-2", "-q:v", "6", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]


def extract_frames(media, start, end, n=6, width=480):
    ts = [start + 1 + (end - start - 2) * i / max(n - 1, 1) for i in range(n)]
    out = []
    for t in ts:
        p = subprocess.run(frame_cmd(media, t, width), capture_output=True)
        if p.returncode == 0 and p.stdout:
            out.append(base64.b64encode(p.stdout).decode())
    return out


def combine(c):
    """Weighted mean of the scores that exist (each 0-10), so a missing stage never counts as zero."""
    parts = [(W_LOCAL, c["local_score10"])]
    if c.get("gemini"):
        parts.append((W_GEMINI, c["gemini"]["score"]))
    if c.get("watch"):
        parts.append((W_WATCH, c["watch"]["score"]))
    return round(sum(w * s for w, s in parts) / sum(w for w, _ in parts), 2)


def make_candidate(i, w, offset):
    ok, why = rank_local.payoff_gate(w)
    loc = rank_local.score(w)
    lift = w.peak_rms_db - w.mean_rms_db
    return {"id": i, "start": w.start, "end": w.end, "peak_t": w.peak_t,
            "stream_start": round(offset + w.start, 1), "stream_end": round(offset + w.end, 1),
            "text": w.text, "speech_pct": round(w.speech_ratio * 100), "audio_lift_db": round(lift, 1),
            "motion_sd": round(w.motion_lift, 1), "heat": w.heat, "gate_ok": ok, "gate": why,
            "local_score": round(loc, 2), "local_score10": round(_num(loc / SCORE_MAX * 10), 2),
            "reasons": analyze.reasons(w), "notes": [], "gemini": None, "watch": None}


def evaluate(wins, offset, gen=gemini.generate, frames_fn=None, top_score=5, top_watch=3, log=print):
    """wins: Windows with .text filled. Returns candidates, best first. Gate failures sort last."""
    cands = [make_candidate(i, w, offset) for i, w in enumerate(sorted(wins, key=lambda w: -w.heat))]
    order = sorted(cands, key=lambda c: (c["gate_ok"], c["local_score"]), reverse=True)
    scored = order[:top_score]
    down = False
    if scored:
        try:
            for cid, g in gemini_scores(scored, gen).items():
                for c in scored:
                    if c["id"] == cid:
                        c["gemini"] = g
        except (gemini.GeminiUnavailable, ValueError, KeyError, TypeError) as e:
            down = isinstance(e, gemini.GeminiUnavailable)   # same service: do not hammer it again
            log(f"gemini scoring unavailable ({type(e).__name__}: {str(e)[:120]}); local score only")
            for c in scored:
                c["notes"].append("gemini scoring unavailable")
    if frames_fn and not down:
        pool = sorted(scored, key=lambda c: (c["gate_ok"], combine(c)), reverse=True)[:top_watch]
        for c in pool:
            try:
                frames = frames_fn(c)
                if not frames:
                    raise ValueError("no frames")
                c["watch"] = gemini_watch(frames, gen)
            except (gemini.GeminiUnavailable, ValueError, KeyError, TypeError) as e:
                log(f"watch gate unavailable for id={c['id']} ({type(e).__name__}: {str(e)[:120]})")
                c["notes"].append("watch gate unavailable")
    for c in cands:
        c["combined"] = combine(c)
    return sorted(cands, key=lambda c: (c["gate_ok"], c["combined"]), reverse=True)


def report(cands, offset, top=3):
    lines = [f"TOP {min(top, len(cands))} CANDIDATES (file seconds | stream seconds = {offset:g} + file, "
             "+/- a few s because the cut starts on a keyframe)"]
    for n, c in enumerate(cands[:top], 1):
        lines.append(f'#{n} id={c["id"]} file {c["start"]:.0f}-{c["end"]:.0f}s | stream '
                     f'{c["stream_start"]:.0f}-{c["stream_end"]:.0f}s | combined {c["combined"]} '
                     f'(local {c["local_score10"]}, gemini {c["gemini"]["score"] if c["gemini"] else "n/a"}, '
                     f'watch {c["watch"]["score"] if c["watch"] else "n/a"}) | gate: {c["gate"]}')
        lines.append("   signals: " + "; ".join(c["reasons"]))
        if c["gemini"]:
            lines.append(f'   gemini: {c["gemini"]["reason"]}' +
                         (f' | hook idea: {c["gemini"]["hook"]}' if c["gemini"]["hook"] else ""))
        if c["watch"]:
            lines.append(f'   watched: {c["watch"]["what"]} | facecam: {c["watch"]["facecam"]} | '
                         f'problems: {c["watch"]["problems"]}')
        if c["text"]:
            lines.append(f'   said: "{c["text"][:160]}"')
        if c["notes"]:
            lines.append("   notes: " + ", ".join(c["notes"]))
    return "\n".join(lines)
