"""Cold-open hook: a short clean line visible from the first frame, in the same font as the captions."""
import re

from .config import HOOK_MAX_SEC
from . import captions

HOOK_SHOW = 2.6      # seconds the hook stays up
MAX_WORDS = 6
MIN_WORD_S = 0.08     # shorter "words" are ASR slivers, never a hook
MAX_PAUSE = 0.8       # a longer pause ends the hook line
# a hook that ends on one of these is cut off mid-thought
DANGLING = set("a an the and or but so to of in on at with for from by as if than then because my your his her their "
               "our its is are was were am be been into onto about like who which".split())


def clean_hook(text):
    t = re.sub(r"[^\w\s'!?,.\-*]", "", text or "")      # no emoji or symbols (the profanity mask * stays)
    t = re.sub(r"\s+", " ", t).strip().rstrip(".,-")
    words = t.split()
    if not (2 <= len(words) <= MAX_WORDS) or not re.search(r"[A-Za-z]", t):
        return ""
    return t


def last_token(text):
    toks = [x for x in re.sub(r"[^a-z0-9' ]", " ", (text or "").lower()).split() if x]
    return toks[-1] if toks else ""


def is_complete(text):
    """A finished thought: 2-6 words, and it does not end on a word that needs a continuation."""
    n = len((text or "").split())
    return 2 <= n <= MAX_WORDS and last_token(text) not in DANGLING


def trim_dangling(words):
    """Drop trailing function words ("lock the" -> "lock") so the line does not stop mid-thought."""
    ws = list(words)
    while ws and last_token(ws[-1]) in DANGLING:
        ws.pop()
    return ws


def duplicates(hook, first_line, thr=0.8):
    """True when the hook just repeats the first caption line (same words, or one contains the other)."""
    a, b = captions._tok(hook), captions._tok(first_line)
    if not a or not b:
        return False
    sa, sb = set(a), set(b)
    return len(sa & sb) / min(len(sa), len(sb)) >= thr


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
    # only words really spoken in the first seconds: no slivers, and a pause ends the line
    first = [w for w in words if w["s"] < within and (w["e"] - w["s"]) >= MIN_WORD_S]
    line, prev_e = [], None
    for w in first:
        if prev_e is not None and w["s"] - prev_e > MAX_PAUSE:
            break
        line.append(w["w"])
        prev_e = w["e"]
        if w["w"].rstrip().endswith((".", "?", "!")) and len(line) >= 2:
            break
        if len(line) >= MAX_WORDS:
            break
    c = clean_hook(" ".join(trim_dangling(line)))
    if c:
        return c, "speech_first3s", True
    if cand:
        return cand, "given_unverified", False
    return "", "none", False


def add_hook(ass, text, margin_v=300, show=HOOK_SHOW):
    """Add the hook to an ASS script made by captions.to_ass: same font, top lane, from t=0."""
    if not text:
        return ass
    style = (f"Style: Hook,{captions.FONT},{captions.HOOK_SIZE},&H00FFFFFF,&H00000000,&H64000000,0,1,5,1,8,60,60,"
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
