"""Word re-timing: put each caption word where the audio has it.

The main transcript is large-v3 with the VAD filter on: the VAD cuts the silences out, whisper times the words on the
compacted audio, and the times are mapped back. A word that follows a pause or sits under loud sound can come back late.
A second pass over the same clip with the VAD off keeps the real clock. This module compares the two:

A main word is moved to where the VAD-off pass heard the SAME word when ALL of these hold (fixed rules, same for every clip):
  1. same word (comparison form: lower case, edge punctuation off);
  2. both passes sure: p >= MIN_P in each;
  3. the heard word starts EARLIER than the main word by at least MIN_SHIFT and at most MAX_SHIFT seconds;
  4. the heard word lies on speech (VAD speech span, +- SPAN_TOL), when spans are given;
  5. no other caption word sits between the heard time and the main word's old start (order and neighbours stay);
  6. a heard word is used for one main word only (the nearest in time).
Nothing else is changed: no text, no gate, no threshold. Every move is reported. Pure logic, unit-tested without audio."""
from . import captions_verify as cv

MIN_P = 0.5
MIN_SHIFT = 0.30
MAX_SHIFT = 3.0
SPAN_TOL = 0.10
MIN_DUR = 0.10


def _on_speech(a, b, spans, tol=SPAN_TOL):
    if not spans:
        return True
    mid = (a + b) / 2
    return any(s - tol <= mid <= e + tol for s, e in spans)


def retime(words, heard, spans=None, min_p=MIN_P, min_shift=MIN_SHIFT, max_shift=MAX_SHIFT):
    """words: caption words {'w','s','e','p'} (clip clock); heard: words of the VAD-off pass (same clock).
    -> (new words sorted by start, [{'w','from','to','p_main','p_heard'}])."""
    out = sorted((dict(w) for w in words), key=lambda w: w["s"])
    moves, used = [], set()
    for i, w in enumerate(out):
        tk = cv.tok(w["w"])
        if not tk or w.get("p", 1.0) < min_p:
            continue
        best = None
        for j, h in enumerate(heard):
            if j in used or cv.tok(h["w"]) != tk or h.get("p", 0.0) < min_p:
                continue
            shift = w["s"] - h["s"]
            if not (min_shift <= shift <= max_shift):
                continue
            if not _on_speech(h["s"], h["e"], spans):
                continue
            if any(k != i and h["s"] - 0.05 <= (o["s"] + o["e"]) / 2 < w["s"] for k, o in enumerate(out)):
                continue
            if best is None or shift < w["s"] - heard[best]["s"]:
                best = j
        if best is None:
            continue
        h = heard[best]
        used.add(best)
        nxt = min((o["s"] for k, o in enumerate(out) if k != i and o["s"] > h["s"]), default=None)
        ne = max(h["e"], h["s"] + MIN_DUR)
        if nxt is not None:
            ne = min(ne, max(nxt, h["s"] + MIN_DUR))
        moves.append({"w": w["w"], "from": round(w["s"], 3), "to": round(h["s"], 3), "p_main": round(w.get("p", 1.0), 3),
                      "p_heard": round(h.get("p", 0.0), 3)})
        w["s"], w["e"], w["retimed"] = round(h["s"], 3), round(ne, 3), True
    return sorted(out, key=lambda w: w["s"]), moves
