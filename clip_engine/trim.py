"""Where the clip starts and ends. Pure numpy/stdlib, so it is unit-tested on synthetic data.

Start: the first real motion (and audible sound) at or after the asked start, never a frozen or
barely-moving opening. End: a payoff (a named phrase, else a finished exclamation that ends a
run of excitement and is followed by a lull), never a mid-walk cut.
"""
import difflib
import re

import numpy as np

TARGET_LEN = 32.0
MIN_TAIL = 0.1             # a word-end cut keeps at least this much after the word
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


def crop_cols(gray, left, width):
    """Columns of a gray stack (n, h, w) that a crop shows. left/width are fractions of the frame
    width. Motion outside this strip is invisible in the 9:16 output, so it must not start a clip."""
    g = np.asarray(gray)
    w = g.shape[2]
    wc = max(2, min(w, int(round(width * w))))
    x0 = int(min(max(round(left * w), 0), w - wc))
    return g[:, :, x0:x0 + wc]


def visible_start(strip, fps, rms, hop, search=START_SEARCH, secs=3.0, step=0.1, gate=None):
    """Earliest offset t (s, 0..search) at which the SHOWN strip passes the same opening gate the
    grade runs (no frozen picture, real motion, `secs` seconds) and there is sound.
    strip: gray stack (n, h, w) at `fps` from the candidate start. Returns (t or None, info).
    The gate is never loosened: if no offset passes, the caller must skip the moment."""
    if gate is None:
        from . import gates
        gate = gates.opening_gate
    strip = np.asarray(strip)
    k = max(1, int(round(fps / 10.0)))          # the grade samples the opening at 10 fps
    need = int(round(secs * fps))
    tried, t = [], 0.0
    while t <= search + 1e-9:
        i0 = int(round(t * fps))
        seg = strip[i0:i0 + need:k]
        if i0 + need <= len(strip) and len(seg) >= 3:
            ok, detail, info = gate(seg, fps / k)
            tried.append(round(t, 2))
            if ok and audible_at(rms, hop, t):
                return round(t, 3), {"visible_start_s": round(t, 3), "checked": len(tried), "detail": detail,
                                     "audio_ok": True}
        t += step
    return None, {"visible_start_s": None, "checked": len(tried), "audio_ok": False}


def crop_cols_path(gray, lefts, width):
    """Like crop_cols, but the crop moves: lefts[i] is the left edge (fraction of the frame width) for
    frame i. This is the strip a subject-tracked 9:16 crop really shows at each moment."""
    g = np.asarray(gray)
    n, _, w = g.shape
    wc = max(2, min(w, int(round(width * w))))
    out = np.empty((n, g.shape[1], wc), dtype=g.dtype)
    for i in range(n):
        left = lefts[min(i, len(lefts) - 1)] if len(lefts) else 0.0
        x0 = int(min(max(round(left * w), 0), w - wc))
        out[i] = g[i, :, x0:x0 + wc]
    return out


MIN_WORD_S = 0.08        # shorter "words" are ASR slivers, not speech


def _real_words(words):
    return [w for w in words if (w["e"] - w["s"]) >= MIN_WORD_S]


def inside_word(words, t, tol=0.03):
    return any(w["s"] + tol < t < w["e"] - tol for w in _real_words(words))


def speech_follows(words, t, within=3.0, first_by=1.2, need=2):
    """At least `need` real spoken words start in [t, t+within) and the first one within `first_by` s."""
    hit = [w for w in _real_words(words) if t <= w["s"] < t + within]
    return len(hit) >= need and hit[0]["s"] - t <= first_by


def peak_follows(rms, hop, t, span=2.0, lift_db=8.0):
    """A loud peak (>= lift_db over the median level) in [t, t+span)."""
    r = np.asarray(rms, dtype=np.float64)
    live = r[r > -80]
    if live.size == 0:
        return False
    seg = r[int(t / hop):int((t + span) / hop) + 1]
    return bool(seg.size and float(seg.max() - np.median(live)) >= lift_db)


def pick_start(strip, fps, rms, hop, words, t_lo, t_hi, min_left=25.0, secs=3.0, step=0.1, gate=None):
    """Search the WHOLE moment for the first start where the SHOWN strip passes the opening gate and
    speech (else a loud peak) follows, with at least `min_left` s left before t_hi.
    strip: gray stack (n, h, w) of the shown crop from the moment start at `fps`; rms (dBFS, hop s),
    words (s/e/w) and t_lo/t_hi are all on that same clock (0 = moment start). Candidates are a time
    grid plus the start of every word (just before it); never inside a word. The gate is not loosened:
    returns (None, info) when nothing qualifies, and the caller must skip the moment.
    Speech starts are tried first; a peak-only start is the fallback."""
    if gate is None:
        from . import gates
        gate = gates.opening_gate
    strip = np.asarray(strip)
    k = max(1, int(round(fps / 10.0)))                  # the grade samples the opening at 10 fps
    need = int(round(secs * fps))
    last = min(t_hi - min_left, (len(strip) - need) / fps)
    cands = set(round(t_lo + i * step, 3) for i in range(int(max(last - t_lo, -1) / step) + 1))
    cands |= {round(w["s"] - 0.08, 3) for w in _real_words(words) if t_lo <= w["s"] - 0.08 <= last}
    cands = sorted(c for c in cands if t_lo <= c <= last + 1e-9)
    why = {"candidates": len(cands), "mid_word": 0, "shown_strip_frozen": 0, "silent": 0, "no_speech_or_peak": 0}
    gate_ok = {}
    for mode in ("speech", "peak"):
        for t in cands:
            if inside_word(words, t):
                why["mid_word"] += mode == "speech"
                continue
            if t not in gate_ok:
                i0 = int(round(t * fps))
                seg = strip[i0:i0 + need:k]
                gate_ok[t] = gate(seg, fps / k)[0] if len(seg) >= 3 else False
                why["shown_strip_frozen"] += (not gate_ok[t]) and mode == "speech"
            if not gate_ok[t]:
                continue
            if not audible_at(rms, hop, t):
                why["silent"] += mode == "speech"
                continue
            if mode == "speech" and speech_follows(words, t):
                return round(t, 3), {"start_s": round(t, 3), "reason": "speech follows", "left_s": round(t_hi - t, 1), **why}
            if mode == "peak" and peak_follows(rms, hop, t):
                return round(t, 3), {"start_s": round(t, 3), "reason": "loud peak follows (no speech)",
                                     "left_s": round(t_hi - t, 1), **why}
            why["no_speech_or_peak"] += mode == "peak"
    return None, {"start_s": None, **why}


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
        e = min(words[i]["e"] + pad, words[i]["e"] + (nxt[i] - words[i]["e"]) * 0.8)
        if e > hi and words[i]["e"] + MIN_TAIL <= hi:      # the pad runs past the latest allowed end: cut at that end, still after the word
            e = hi
        return e

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
        tight = [w for i, w in enumerate(words) if lo <= w["e"] + 0.0 <= hi + 0.35 and i + 1 < len(words) and nxt[i] - w["e"] < 0.2]
        late = [w for i, w in enumerate(words) if hi < end_for(i) and lo <= w["e"] <= hi + 0.35]
        raise ValueError(f"no word-end cut point between {lo:.1f}s and {hi:.1f}s: skip this moment "
                         f"(words {len(words)}, last word ends {words[-1]['e']:.2f}s, "
                         f"{len(tight)} ends in range rejected for a <0.2 s gap, {len(late)} whose end+pad passes {hi:.1f}s)")
    _, e, i, why = best
    return round(e, 3), "payoff heuristic: " + (", ".join(why) or "nearest gap") + f", after '{words[i]['w']}'", notes


# ---- dense cut: the densest 25-30 s stretch inside a longer window (an option; no gate is touched) ------------------
DENSE_MAX = 30.0
COVER_PAD = 0.10           # same pad as captions_verify.PAD / speech_spans.raw_coverage


def window_coverage(words, t0, t1, pad=COVER_PAD):
    """Share of [t0, t1] that a caption word (+- pad) is on screen for: the same measure as speech_spans.raw_coverage."""
    if t1 <= t0:
        return 0.0
    iv = sorted((max(t0, w["s"] - pad), min(t1, w["e"] + pad)) for w in words if w["e"] + pad > t0 and w["s"] - pad < t1)
    tot, cur = 0.0, None
    for a, b in iv:
        if b <= a:
            continue
        if cur and a <= cur[1]:
            cur[1] = max(cur[1], b)
        else:
            if cur:
                tot += cur[1] - cur[0]
            cur = [a, b]
    if cur:
        tot += cur[1] - cur[0]
    return min(1.0, tot / (t1 - t0))


def valid_starts(strip, fps, rms, hop, words, t_lo, t_hi, min_left=25.0, secs=3.0, gate=None):
    """Every word-anchored start that pick_start would accept in its "speech" mode (same opening gate on the shown strip,
    audible, speech follows, never inside a word, at least min_left s left). Nothing is loosened. -> (sorted starts, info)."""
    if gate is None:
        from . import gates
        gate = gates.opening_gate
    strip = np.asarray(strip)
    k = max(1, int(round(fps / 10.0)))
    need = int(round(secs * fps))
    last = min(t_hi - min_left, (len(strip) - need) / fps)
    cands = sorted({round(w["s"] - 0.08, 3) for w in _real_words(words) if t_lo <= w["s"] - 0.08 <= last})
    why = {"candidates": len(cands), "mid_word": 0, "shown_strip_frozen": 0, "silent": 0, "no_speech": 0}
    out = []
    for t in cands:
        if inside_word(words, t):
            why["mid_word"] += 1
            continue
        i0 = int(round(t * fps))
        seg = strip[i0:i0 + need:k]
        if not (len(seg) >= 3 and gate(seg, fps / k)[0]):
            why["shown_strip_frozen"] += 1
            continue
        if not audible_at(rms, hop, t):
            why["silent"] += 1
            continue
        if not speech_follows(words, t):
            why["no_speech"] += 1
            continue
        out.append(t)
    return out, {**why, "valid": len(out)}


def word_ends(words, lo, hi, pad=0.35, min_gap=0.2):
    """[(end_s, word_index)]: cut points just after a word (same rules as payoff_end: a gap of >= 0.2 s follows) in [lo, hi]."""
    out = []
    for i, w in enumerate(words):
        nxt = words[i + 1]["s"] if i + 1 < len(words) else float("inf")
        if nxt - w["e"] < min_gap and i + 1 < len(words):
            continue
        e = min(w["e"] + pad, w["e"] + (nxt - w["e"]) * 0.8)
        if e > hi and w["e"] + MIN_TAIL <= hi:
            e = hi
        if lo <= e <= hi:
            out.append((e, i))
    return out


DENSE_GAP_TIERS = (0.2, 0.12)      # a pause of 0.2 s first (as payoff_end); non-stop talk: a 0.12 s breath, still after a word end


def dense_cut(words, starts, env_end, min_len=25.0, max_len=DENSE_MAX):
    """Pick (t0, t1) with min_len <= t1-t0 <= max_len from `starts` (valid starts) and word-end cut points, maximising the
    share of the clip covered by words. Ties: a finished sentence, then the earlier start. Never lowers a gate: the caller still
    runs every check. Raises ValueError when no start has a word-end cut point in range. -> (t0, t1, info)."""
    best, n_ends, tier = None, 0, None
    for tier in DENSE_GAP_TIERS:          # natural pauses first; the breath tier only when no start has a pause cut point
        for t0 in starts:
            for e, i in word_ends(words, t0 + min_len, min(t0 + max_len, env_end), min_gap=tier):
                n_ends += 1
                cov = window_coverage(words, t0, e)
                bonus = 1 if words[i]["w"].rstrip().endswith((".", "!", "?")) else 0
                key = (round(cov, 4), bonus, -t0)
                if best is None or key > best[0]:
                    best = (key, t0, e, i, cov)
        if best is not None:
            break
    if best is None:
        raise ValueError(f"dense cut: no word-end cut point {min_len:g}-{max_len:g} s after any of {len(starts)} valid starts "
                         f"(window ends {env_end:.1f}s): skip this moment")
    _, t0, e, i, cov = best
    return round(t0, 3), round(e, 3), {"mode": "dense", "coverage_planned": round(cov, 3), "starts": len(starts),
                                       "ends_tried": n_ends, "min_gap_s": tier, "after_word": words[i]["w"]}
