"""Cold-open hook: a short clean line visible from the first frame, in the same font as the captions."""
import re

from .config import HOOK_MAX_SEC

HOOK_SHOW = 2.6      # seconds the hook stays up
MAX_WORDS = 6


def clean_hook(text):
    t = re.sub(r"[^\w\s'!?,.\-]", "", text or "")       # no emoji or symbols
    t = re.sub(r"\s+", " ", t).strip().rstrip(".,-")
    words = t.split()
    if not (2 <= len(words) <= MAX_WORDS) or not re.search(r"[A-Za-z]", t):
        return ""
    return t


def pick_hook(gemini_hook, words):
    """Gemini's idea if it is usable, else the shortest strong line the streamer actually says."""
    h = clean_hook(gemini_hook)
    if h:
        return h, "gemini"
    line, best = [], ""
    for w in words:
        line.append(w["w"])
        if w["w"].rstrip().endswith((".", "?", "!")):
            c = clean_hook(" ".join(line))
            if c and (not best or len(c) < len(best)) and len(c.split()) >= 3:
                best = c
            line = []
    return (best, "speech") if best else ("", "none")


def _tok(t):
    return [x for x in re.sub(r"[^a-z0-9' ]", " ", (t or "").lower()).split() if x]


def overlap(text, words, within=3.0):
    """Share of the hook's words that are actually spoken in the first `within` seconds (fuzzy)."""
    import difflib
    toks = _tok(text)
    heard = [x for w in words if w["s"] < within for x in _tok(w["w"])]
    if not toks or not heard:
        return 0.0
    hit = sum(any(difflib.SequenceMatcher(None, t, h).ratio() >= 0.8 for h in heard) for t in toks)
    return hit / len(toks)


def choose_hook(given, words, within=3.0, min_overlap=0.6):
    """Hook that matches the first seconds. Returns (text, source, matched).
    given and spoken in the first `within` s -> kept; else the first spoken line from those seconds;
    else the given text flagged unverified (it may describe something visual); else none."""
    cand = clean_hook(given)
    if cand and overlap(cand, words, within) >= min_overlap:
        return cand, "given", True
    first = [w for w in words if w["s"] < within]
    line = []
    for w in first:
        line.append(w["w"])
        if w["w"].rstrip().endswith((".", "?", "!")) and len(line) >= 2:
            break
        if len(line) >= MAX_WORDS:
            break
    c = clean_hook(" ".join(line))
    if c:
        return c, "speech_first3s", True
    if cand:
        return cand, "given_unverified", False
    return "", "none", False


def add_hook(ass, text, margin_v=300, show=HOOK_SHOW):
    """Add the hook to an ASS script made by captions.to_ass: same font, top lane, from t=0."""
    if not text:
        return ass
    style = ("Style: Hook,DejaVu Sans,72,&H00FFFFFF,&H00000000,&H64000000,1,1,5,1,8,60,60,"
             f"{margin_v}\n")
    head, sep, rest = ass.partition("\n[Events]")
    head = head.rstrip("\n") + "\n" + style
    row = f"Dialogue: 1,0:00:00.00,0:00:{show:05.2f},Hook,{text}\n"
    return head + "\n" + sep.lstrip("\n") + rest.rstrip("\n") + "\n" + row


def hook_start(ass):
    for line in ass.splitlines():
        if line.startswith("Dialogue") and ",Hook," in line:
            return line.split(",")[1]
    return None


def within_budget(start_str):
    h, m, s = start_str.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s) <= HOOK_MAX_SEC
