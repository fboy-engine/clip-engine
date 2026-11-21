"""Cleanup of the final-clip transcript (large-v3) before it becomes captions."""
import difflib
import re

MIN_P = 0.15
# stems are matched on the whole word (so "duck" and "classic" never match)
PROFANE = re.compile(r"^(motherfuck\w*|fuck\w*|bullshit\w*|shit\w*|bitch\w*|asshole\w*|cunt\w*|dick(s|head)?|"
                     r"pussy|pussies|bastard\w*|whore\w*|slut\w*|nigg\w*|fag(s|got|gots)?)$", re.I)
_CORE = re.compile(r"^(\W*)(\w[\w']*)(\W*)$")


def flatten(segments):
    return [dict(w) for s in segments for w in s["words"]]


def mask_word(text):
    """'fuck' -> 'f***', 'Shit!' -> 'S***!', 'fucking,' -> 'f******,'. First letter kept, punctuation kept."""
    m = _CORE.match(text or "")
    if not m or not PROFANE.match(m.group(2)):
        return text
    core = m.group(2)
    return m.group(1) + core[0] + "*" * (len(core) - 1) + m.group(3)


def mask_text(text):
    return " ".join(mask_word(t) for t in (text or "").split(" "))


def mask_profanity(words):
    """Word list with profanity masked; times and every other field are untouched."""
    return [{**w, "w": mask_word(w["w"])} for w in words]


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


def _norm(t):
    return re.sub(r"^\W+|\W+$", "", t or "").lower()


def apply_reference(words, reference, min_coverage=0.5, max_block=3):
    """Correct names and odd words with the on-screen text (OCR of the game's own subtitles).
    Only same-length replace blocks are touched, and only when the reference really is this speech
    (>= min_coverage of the words line up). Times never change. Returns (words, n_replaced)."""
    toks = (reference or "").split()
    if not toks or not words:
        return words, 0
    a, b = [_norm(w["w"]) for w in words], [_norm(t) for t in toks]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops = sm.get_opcodes()
    same = sum(i2 - i1 for tag, i1, i2, _, _ in ops if tag == "equal")
    if same / len(a) < min_coverage:
        return words, 0
    out, n = [dict(w) for w in words], 0
    for tag, i1, i2, j1, j2 in ops:
        if tag != "replace" or (i2 - i1) != (j2 - j1) or (i2 - i1) > max_block:
            continue
        for k in range(i2 - i1):
            old = out[i1 + k]["w"]
            core_old = re.sub(r"^\W+|\W+$", "", old)
            core_new = re.sub(r"^\W+|\W+$", "", toks[j1 + k])
            if core_old and core_new and core_old != core_new:
                out[i1 + k]["w"] = old.replace(core_old, core_new, 1)
                n += 1
    return out, n


def to_clip_time(words, offset, min_inside=0.5, min_inside_s=0.15):
    """Shift word times onto the clip clock. Words fully before the cut are dropped; a word the cut
    slices through is kept only when most of it is still in the clip (otherwise a 30 ms caption
    sliver like "bro" would open the clip and the hook)."""
    res = []
    for w in words:
        s, e = w["s"] - offset, w["e"] - offset
        if e <= 0:
            continue
        if s < 0:
            inside = e / max(w["e"] - w["s"], 1e-6)
            if inside < min_inside or e < min_inside_s:
                continue
        res.append({**w, "s": max(s, 0.0), "e": e})
    return res
