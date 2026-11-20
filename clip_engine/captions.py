"""One clean word-timed caption style (ASS). At most one highlighted word per line."""
STOP = set("a an the and or but so to of in on at is it i you we he she they this that "
           "was be for with as are my your".split())
HL_ON, HL_OFF = "{\\c&H0000D7FF&}", "{\\c&HFFFFFF&}"


def pick_highlight(words):
    cand = [w for w in words if w.lower().strip(".,!?") not in STOP and len(w) > 3]
    return max(cand, key=len) if cand else None


def group_words(words, max_words=4, max_gap=0.6):
    """words: [{'w','s','e'}] -> lines of <=max_words, split on silence gaps."""
    lines, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w["s"] - cur[-1]["e"] > max_gap):
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def _t(sec):
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def to_ass(words, offset=0.0, w=1080, h=1920, margin_v=420):
    hdr = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\n\n"
           "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,"
           "BackColour,Bold,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV\n"
           "Style: Main,DejaVu Sans,64,&H00FFFFFF,&H00000000,&H64000000,1,1,4,1,2,60,60,"
           f"{margin_v}\n\n[Events]\nFormat: Layer,Start,End,Style,Text\n")
    rows = []
    for line in group_words(words):
        hl = pick_highlight([x["w"] for x in line])
        txt = " ".join((HL_ON + x["w"] + HL_OFF) if hl and x["w"] == hl else x["w"]
                       for x in line)
        s, e = line[0]["s"] - offset, line[-1]["e"] - offset
        if e > 0:
            rows.append(f"Dialogue: 0,{_t(max(s, 0))},{_t(e)},Main,{txt}")
    return hdr + "\n".join(rows) + "\n"
