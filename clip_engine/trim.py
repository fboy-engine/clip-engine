"""Where the clip starts and ends. Pure numpy/stdlib, so it is unit-tested on synthetic data.

Start: the first real motion (and audible sound) at or after the asked start, never a frozen or
barely-moving opening. End: a payoff (a named phrase, else a finished exclamation that ends a
run of excitement and is followed by a lull), never a mid-walk cut.
"""
import difflib
import re

import numpy as np

TARGET_LEN = 32.0
START_SEARCH = 4.0       # s after the asked start to look for the first real motion
MOTION_FLOOR = 0.0025    # mean abs frame diff (0..1) that counts as movement at 20 fps
FROZEN = 0.0010          # below this two frames are "the same picture"
AUDIO_FLOOR_DB = -45.0   # the first 0.3 s of the clip must be at least this loud


def motion_diffs(gray, rect=None):
    """Per-frame mean abs diff vs the previous frame (0..1) on a gray stack (n, h, w).
    rect = (x0, y0, x1, y1) fractions to ignore (the facecam always moves). d[0] = 0."""
    f = np.asarray(gray, dtype=np.float32) / 255.0
    if len(f) < 2:
        return np.zeros(len(f))
    d = np.abs(np.diff(f, axis=0))
    if rect:
        h, w = d.shape[1:]
        keep = np.ones((h, w), dtype=bool)
        keep[int(rect[1] * h):int(np.ceil(rect[3] * h)), int(rect[0] * w):int(np.ceil(rect[2] * w))] = False
        if keep.any():
            d = d[:, keep]
    return np.concatenate([[0.0], d.reshape(len(d), -1).mean(axis=1)])


def longest_run(mask):
    best = run = 0
    for v in mask:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def first_motion(d, fps, floor=MOTION_FLOOR, frozen=FROZEN, sustain=1.0, min_active=0.7, max_frozen=0.25):
    """Index of the first frame that starts `sustain` seconds of real movement: at least
    `min_active` of frames above `floor` and no frozen run longer than `max_frozen` s. None if none."""
    d = np.asarray(d, dtype=np.float64)
    k = max(2, int(round(sustain * fps)))
    for i in range(1, len(d) - k + 1):
        w = d[i:i + k]
        if d[i] >= floor and (w >= floor).mean() >= min_active and longest_run(w < frozen) / fps <= max_frozen:
            return i
    return None


def audible_at(rms, hop, t, need_db=AUDIO_FLOOR_DB, span=0.3):
    """True when the 50 ms RMS series (dBFS, hop seconds) is audible from t to t+span."""
    r = np.asarray(rms, dtype=np.float64)
    i0, i1 = int(t / hop), max(int(t / hop) + 1, int((t + span) / hop))
    seg = r[i0:i1]
    return bool(seg.size and seg.mean() >= need_db)


def choose_start(d, fps, rms, hop, search=START_SEARCH):
    """d: motion diffs from the asked start at `fps`; rms: dBFS series from the asked start at `hop`.
    Returns (offset_seconds_from_asked_start or None, info). Skips motion that begins in silence."""
    n = int(search * fps)
    d = np.asarray(d, dtype=np.float64)
    pos, tried = 0, []
    while pos < min(n, len(d)):
        i = first_motion(d[pos:], fps)
        if i is None:
            break
        t = (pos + i) / fps
        if t > search:
            break
        tried.append(round(t, 3))
        if audible_at(rms, hop, t):
            return t, {"motion_start_s": round(t, 3), "tried": tried, "audio_ok": True}
        pos += i + 1
    return None, {"tried": tried, "audio_ok": False}


def snap_start_forward(words, t, max_fwd=1.0, lead=0.08):
    """If t falls inside a word, move to just before the next word when that is within max_fwd s.
    Never moves earlier (an earlier start would bring the dead opening back)."""
    for a, b in zip(words, words[1:] + [None]):
        if a["s"] < t < a["e"]:
            nxt = (b["s"] - lead) if b else a["e"] + lead
            if 0 < nxt - t <= max_fwd:
                return round(max(nxt, t), 3), f"start moved +{nxt - t:.2f}s off a word"
            return t, "start is inside a word (no clean gap within 1 s)"
    return t, ""


# ---- end -------------------------------------------------------------------

def _norm(w):
    return re.sub(r"[^a-z0-9']", "", (w or "").lower())


def match_phrase(words, phrase, min_ratio=0.8, need=0.6):
    """Find `phrase` in `words` (fuzzy, in order). Returns (i_first, i_last, score) or None."""
    toks = [_norm(t) for t in (phrase or "").split() if _norm(t)]
    ws = [_norm(w["w"]) for w in words]
    if not toks or not ws:
        return None
    best = None
    for i in range(len(ws)):
        j, hit, last = i, 0, None
        for t in toks:
            for jj in range(j, min(j + 3, len(ws))):
                if difflib.SequenceMatcher(None, t, ws[jj]).ratio() >= min_ratio:
                    hit, last, j = hit + 1, jj, jj + 1
                    break
        score = hit / len(toks)
        if last is not None and score >= need and (best is None or score > best[2] or (score == best[2] and last < best[1])):
            best = (i, last, score)
    return best


def payoff_end(words, start, lo, hi, rms=None, hop=0.05, phrase="", pad=0.35, rms_t0=None):
    """Pick the clip end (source-file seconds) in [lo, hi]. `start` is the clip start; rms (dBFS,
    hop s) begins at rms_t0 (default start). Returns (end, reason, notes)."""
    notes = []
    rms_t0 = start if rms_t0 is None else rms_t0
    if not words:
        raise ValueError("no words: cannot place a payoff end")
    nxt = {i: (words[i + 1]["s"] if i + 1 < len(words) else float("inf")) for i in range(len(words))}

    def end_for(i):
        return min(words[i]["e"] + pad, words[i]["e"] + (nxt[i] - words[i]["e"]) * 0.8)

    if phrase:
        m = match_phrase(words, phrase)
        if m:
            e = end_for(m[1])
            if lo <= e <= hi:
                return round(e, 3), f"payoff phrase matched ({m[2]:.0%}): ends after '{words[m[1]]['w']}'", notes
            notes.append(f"payoff phrase found but its end {e:.1f}s is outside {lo:.1f}-{hi:.1f}s")
        else:
            notes.append("payoff phrase not found in the transcript; using the heuristic")
    r = np.asarray(rms if rms is not None else [], dtype=np.float64)
    base = float(np.median(r[r > -80])) if (r > -80).any() else None

    def rseg(a, b):
        seg = r[max(int((a - rms_t0) / hop), 0):max(int((b - rms_t0) / hop), 0)]
        return seg if seg.size else None

    best = None
    for i, w in enumerate(words):
        if nxt[i] - w["e"] < 0.2 and i + 1 < len(words):
            continue
        e = end_for(i)
        if not (lo <= e <= hi):
            continue
        txt = w["w"].rstrip()
        sc, why = 0.0, []
        if txt.endswith(("!", "?")):
            sc += 2.0
            why.append("finished exclamation/question")
        elif txt.endswith("."):
            sc += 1.0
            why.append("sentence end")
        pre, post = rseg(w["e"] - 3.0, w["e"]), rseg(w["e"], w["e"] + 1.5)
        if pre is not None and base is not None:
            lift = float(pre.max() - base)
            sc += float(np.clip(lift / 6.0, 0, 2))
            why.append(f"+{lift:.0f} dB peak in the last 3 s")
            if post is not None and float(pre.mean() - post.mean()) >= 6:
                sc += 1.0
                why.append("audio drops after")
        gap = min(nxt[i] - w["e"], 2.0)
        sc += gap / 2.0
        sc -= abs((e - start) - TARGET_LEN) / 20.0
        if best is None or sc > best[0]:
            best = (sc, e, i, why)
    if best is None:
        raise ValueError(f"no word-end cut point between {lo:.1f}s and {hi:.1f}s: skip this moment")
    _, e, i, why = best
    return round(e, 3), "payoff heuristic: " + (", ".join(why) or "nearest gap") + f", after '{words[i]['w']}'", notes
