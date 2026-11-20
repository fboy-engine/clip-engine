"""Clean cut points: never cut through a word; prefer ending after a finished sentence."""
from .config import MIN_LEN, MAX_LEN

END = (".", "?", "!")


def gaps(words, min_gap=0.12):
    """[(end_of_word, start_of_next)] where there is room to cut."""
    return [(a["e"], b["s"]) for a, b in zip(words, words[1:]) if b["s"] - a["e"] >= min_gap]


def select_clean(words, start, end, radius=2.5, pad_in=0.15, pad_out=0.35):
    """words: [{'w','s','e'}] on the same clock as start/end. Returns (start, end, notes).
    Start moves into the nearest gap (just before the next word); end into the nearest gap
    after a sentence end when one is close, else the nearest gap. Falls back to the input
    bounds if the result would leave MIN_LEN..MAX_LEN."""
    notes = []
    s2, e2 = start, end
    inside = [w for w in words if w["s"] < start < w["e"]]
    if inside:
        near = [g for g in gaps(words) if abs(g[1] - start) <= radius]
        if near:
            a, b = min(near, key=lambda g: abs(g[1] - start))
            s2 = max(a, b - pad_in)
            notes.append(f"start moved {s2 - start:+.2f}s off a word")
    cut = [w for w in words if w["s"] < end < w["e"]]
    ends = [(w, nxt) for w, nxt in zip(words, words[1:]) if nxt["s"] - w["e"] >= 0.12 and abs(w["e"] - end) <= radius]
    sentence = [(w, n) for w, n in ends if w["w"].rstrip().endswith(END)]
    pool = sentence or ends
    if cut or sentence:
        if pool:
            w, n = min(pool, key=lambda p: abs(p[0]["e"] - end))
            e2 = min(n["s"], w["e"] + pad_out)
            notes.append(f"end moved {e2 - end:+.2f}s " + ("to a sentence end" if sentence else "off a word"))
    if not (MIN_LEN <= e2 - s2 <= MAX_LEN):
        notes.append("kept original bounds (cleaner cut would break 25-45 s)")
        return start, end, notes
    return round(s2, 3), round(e2, 3), notes
