"""Cleanup of the final-clip transcript (large-v3) before it becomes captions."""
import re

MIN_P = 0.15


def flatten(segments):
    return [dict(w) for s in segments for w in s["words"]]


def fix_words(words, glossary=None):
    """Drop hallucinated or empty words, glue stray punctuation, apply a name glossary
    ({'lucia': 'Lucia'}). Times are untouched, so captions stay in sync."""
    glossary = {k.lower(): v for k, v in (glossary or {}).items()}
    out = []
    for w in words:
        t = (w.get("w") or "").strip()
        if not t or not re.search(r"\w", t):
            if out and t:
                out[-1]["w"] += t
            continue
        if w.get("p", 1.0) < MIN_P and (w["e"] - w["s"]) > 2.0:      # long, unsure: hallucination
            continue
        core = re.sub(r"^\W+|\W+$", "", t).lower()
        if core in glossary:
            t = t.replace(re.sub(r"^\W+|\W+$", "", t), glossary[core])
        out.append({**w, "w": t})
    return out


def to_clip_time(words, offset):
    """Shift word times onto the clip clock and drop words fully outside [0, inf)."""
    res = []
    for w in words:
        s, e = w["s"] - offset, w["e"] - offset
        if e > 0:
            res.append({**w, "s": max(s, 0.0), "e": e})
    return res
