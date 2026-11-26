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
    words = []
    for x in t.split():                     # ASR stutter: "okay okay" shows as one word
        if not words or re.sub(r"\W", "", x.lower()) != re.sub(r"\W", "", words[-1].lower()):
            words.append(x)
    t = " ".join(words)
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


# ---- phrase hook (A1): one complete phrase, never a piece of caption 1, never a copy of it -------------------------
SPOKEN_WITHIN = 40.0     # the hook phrase must really be said somewhere in the clip


def sentences(words, max_pause=MAX_PAUSE):
    """Split word dicts into sentences: end punctuation (. ? !) or a long pause ends one."""
    out, cur = [], []
    for w in words:
        if cur and w["s"] - cur[-1]["e"] > max_pause:
            out.append(cur)
            cur = []
        cur.append(w)
        if w["w"].rstrip().endswith((".", "?", "!")):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def clauses(sentence):
    """The whole sentence plus its comma-separated clauses (each a candidate), as lists of word dicts."""
    outs, cur = [list(sentence)], []
    for w in sentence:
        cur.append(w)
        if w["w"].rstrip().endswith(","):
            outs.append(cur)
            cur = []
    if cur and len(cur) < len(sentence):
        outs.append(cur)
    return outs


def first_line_text(words):
    g = captions.group_words(words)
    return " ".join(x["w"] for x in g[0]) if g else ""


def _score(cand, start):
    txt = " ".join(w["w"] for w in cand)
    n = len(cand)
    sc = (2.0 if txt.rstrip().endswith(("?", "!")) else 0.0) + (1.0 if 4 <= n <= 6 else 0.0)
    sc += 0.5 if captions.pick_highlight([w["w"] for w in cand]) else 0.0
    return sc - 0.05 * start


# words that must never be in the hook line (a Reel can get flagged for them); they stay in the captions as spoken.
# Slurs, sexual and drug words, self-harm and hate-group words. A masked word (f***) is never a hook word either.
UNSAFE = re.compile(r"^(gay\w*|homo\w*|lesbian\w*|trann\w*|retard\w*|spastic\w*|crack|cocaine|heroin|meth|porn\w*|sex\w*|nude\w*|naked|"
                    r"rape\w*|rapist\w*|suicid\w*|nazi\w*|terroris\w*|jew\w*|slave\w*|bum|ass|asses|arse|piss\w*|crap|cock\w*|"
                    r"penis|vagina|boob\w*|tits?|cum|anal|nuts|balls|damn\w*|hell)$", re.I)
LOCAL_PAUSE = 0.25       # a pause this long splits the speech into runs for the local fallback
LOCAL_MIN_WORDS = 3
FRAGMENT_START = {"and", "but", "so", "or", "because", "then", "which", "that", "uh", "um", "like", "wait", "a", "an", "the", "as", "well",
                  "of", "to", "in", "on", "at", "for", "with", "by", "from", "is", "are", "was", "were", "be"}
# a local line must not stop on one of these either (words that open the next clause)
LOCAL_DANGLING = {"before", "after", "when", "while", "where", "what", "how", "why", "also", "just", "really", "very", "gonna", "going",
                  "not", "no", "yeah", "okay", "ok", "well", "now"}


def unsafe(text):
    """True when any word of the line could get the Reel flagged (see UNSAFE) or is a masked word."""
    for t in (text or "").split():
        if "*" in t or UNSAFE.match(re.sub(r"^\W+|\W+$", "", t)):
            return True
    return False


def runs(words, pause=LOCAL_PAUSE):
    """Split word dicts into runs at a pause or at punctuation (. ? ! , ; :)."""
    out, cur = [], []
    for w in words:
        if (w["e"] - w["s"]) < MIN_WORD_S:
            continue
        if cur and w["s"] - cur[-1]["e"] > pause:
            out.append(cur)
            cur = []
        cur.append(w)
        if w["w"].rstrip().endswith((".", "?", "!", ",", ";", ":")):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def local_phrase(words, first, max_start=20.0):
    """No punctuation and no pauses to lean on (local ASR on fast speech): the best 3-6 word window inside a run of the
    speech itself. Deterministic: same words in, same hook out. Never ends on a dangling word, never starts on a
    connector, never contains an unsafe word, never repeats the first caption line. Returns text or ''."""
    best, best_sc = "", None
    for run in runs(words):
        if run[0]["s"] > max_start:
            break
        n = len(run)
        for size in range(min(MAX_WORDS, n), LOCAL_MIN_WORDS - 1, -1):
            for i in range(0, n - size + 1):
                c = run[i:i + size]
                txt = clean_hook(" ".join(w["w"] for w in c))
                if not txt or not is_complete(txt) or unsafe(txt) or duplicates(txt, first):
                    continue
                if _tok(txt)[0] in FRAGMENT_START or last_token(txt) in LOCAL_DANGLING:
                    continue
                sc = (1.0 if 4 <= size <= 6 else 0.0) + (1.5 if i + size == n else 0.0) + (1.0 if i == 0 else 0.0)
                sc += 2.0 if txt.rstrip().endswith(("?", "!")) else 0.0
                sc += 0.5 if captions.pick_highlight([w["w"] for w in c]) else 0.0
                sc -= 0.05 * c[0]["s"]
                if best_sc is None or sc > best_sc + 1e-9:
                    best, best_sc = txt, sc
    return best


def phrase_hook(given, words, max_start=20.0):
    """-> (text, source, matched). ONE complete phrase of 2-6 words that
    - is a whole sentence or comma clause (starts and ends on a boundary, never a cut piece),
    - does not repeat or split the first caption line,
    - is really spoken in the clip (matched=True), or is Gemini's own line flagged given_unverified.
    The captions keep every word; the hook never takes words away from them."""
    first = first_line_text(words)
    cand = clean_hook(given)
    if cand and is_complete(cand) and not duplicates(cand, first) and not unsafe(cand):
        if overlap(cand, words, within=SPOKEN_WITHIN) >= 0.6:
            return cand, "given", True
    best, best_sc = None, None
    for sent in sentences(words):
        if sent[0]["s"] > max_start:
            break
        for c in clauses(sent):
            c = [w for w in c if (w["e"] - w["s"]) >= MIN_WORD_S]
            txt = clean_hook(" ".join(w["w"] for w in c))
            if not txt or not is_complete(txt) or duplicates(txt, first) or unsafe(txt):
                continue
            # a clause that starts mid-thought (after a conjunction) is a fragment
            if _tok(txt)[0] in {"and", "but", "so", "or", "because", "then", "which", "that"} and len(c) < len(sent):
                continue
            sc = _score(c, c[0]["s"])
            if best_sc is None or sc > best_sc:
                best, best_sc = txt, sc
    if best:
        return best, "speech_phrase", True
    local = local_phrase(words, first, max_start)
    if local:
        return local, "speech_local", True
    if cand and is_complete(cand) and not duplicates(cand, first) and not unsafe(cand):
        return cand, "given_unverified", False
    return "", "none", False
