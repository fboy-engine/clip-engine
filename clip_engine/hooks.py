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
