"""One clean word-timed caption style (ASS). At most one highlighted word per line.

The font is bundled in clip_engine/fonts (Anton, SIL OFL 1.1) and handed to libass with fontsdir, so a render
never depends on what the runner has installed."""
import os
import re

FONT = "Anton"
FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
MAIN_SIZE, HOOK_SIZE = 84, 76
LINE_K = 1.25                 # line height as a multiple of the font size (used for text bands)
SEAM_Y = 540                  # where the cam block ends and the gameplay panel starts
STOP = set("a an the and or but so to of in on at is it i you we he she they this that "
           "was be for with as are my your".split())
HL_ON, HL_OFF = "{\\c&H0000D7FF&}", "{\\c&HFFFFFF&}"


def pick_highlight(words):
    cand = [w for w in words if w.lower().strip(".,!?*") not in STOP and len(w) > 3]
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


def _sec(t):
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def to_ass(words, offset=0.0, w=1080, h=1920, margin_v=420, seam=None):
    """seam=None: captions at the bottom lane (margin_v from the bottom). seam=y: every caption line is
    centred on that row (the seam between the cam block and the gameplay panel)."""
    align, mv = (5, 0) if seam is not None else (2, margin_v)
    hdr = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\n\n"
           "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,"
           "BackColour,Bold,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV\n"
           f"Style: Main,{FONT},{MAIN_SIZE},&H00FFFFFF,&H00000000,&H64000000,0,1,4,1,{align},60,60,"
           f"{mv}\n\n[Events]\nFormat: Layer,Start,End,Style,Text\n")
    pos = f"{{\\an5\\pos({w // 2},{int(seam)})}}" if seam is not None else ""
    rows = []
    for line in group_words(words):
        hl = pick_highlight([x["w"] for x in line])
        parts, done = [], False
        for x in line:                     # highlight ONLY the first occurrence: a repeated word is not a 2nd highlight
            if hl and not done and x["w"] == hl:
                parts.append(HL_ON + x["w"] + HL_OFF)
                done = True
            else:
                parts.append(x["w"])
        txt = " ".join(parts)
        s, e = line[0]["s"] - offset, line[-1]["e"] - offset
        if e > 0:
            rows.append(f"Dialogue: 0,{_t(max(s, 0))},{_t(e)},Main,{pos}{txt}")
    return hdr + "\n".join(rows) + "\n"


def plain_lines(ass, style="Main"):
    """Text of each Dialogue row of `style`, tags removed, in time order."""
    out = []
    for l in ass.splitlines():
        if l.startswith("Dialogue:"):
            f = l[len("Dialogue:"):].strip().split(",", 4)
            if f[3] == style:
                out.append((_sec(f[1]), re.sub(r"\{[^}]*\}", "", f[4]).strip()))
    return [t for _, t in sorted(out)]


def ass_fonts(ass):
    """Font names used by the styles of an ASS script."""
    names, in_styles = set(), False
    for l in ass.splitlines():
        if l.startswith("[V4+ Styles]"):
            in_styles = True
        elif l.startswith("[") and in_styles:
            in_styles = False
        elif in_styles and l.startswith("Style:"):
            names.add(l.split(":", 1)[1].split(",")[1].strip())
    return sorted(names)


def ass_bands(ass, h=1920):
    """[(style, t_start, t_end, y_top, y_bottom)] for every Dialogue row: where its text sits on the frame.
    One text line is assumed (the caption groups are short); \\pos with \\an5 centres on y."""
    styles, in_styles = {}, False
    for l in ass.splitlines():
        if l.startswith("[V4+ Styles]"):
            in_styles = True
        elif l.startswith("[") and in_styles:
            in_styles = False
        elif in_styles and l.startswith("Style:"):
            f = [x.strip() for x in l.split(":", 1)[1].split(",")]
            styles[f[0]] = {"size": float(f[2]), "align": int(f[10]), "mv": float(f[13])}
    bands = []
    for l in ass.splitlines():
        if not l.startswith("Dialogue:"):
            continue
        f = l[len("Dialogue:"):].strip().split(",", 4)
        st = styles.get(f[3])
        if not st:
            continue
        hh = st["size"] * LINE_K
        pos = re.search(r"\\pos\(\s*[-\d.]+\s*,\s*([-\d.]+)\s*\)", f[4])
        if pos and "\\an5" in f[4]:
            y = float(pos.group(1))
            y0, y1 = y - hh / 2, y + hh / 2
        elif st["align"] in (7, 8, 9):
            y0, y1 = st["mv"], st["mv"] + hh
        elif st["align"] in (4, 5, 6):
            y0, y1 = h / 2 - hh / 2, h / 2 + hh / 2
        else:
            y1 = h - st["mv"]
            y0 = y1 - hh
        bands.append((f[3], _sec(f[1]), _sec(f[2]), round(y0, 1), round(y1, 1)))
    return bands


def band_overlaps(bands):
    """Pairs of text bands that are on screen at the same time and share rows (text over text)."""
    bad = []
    for i, a in enumerate(bands):
        for b in bands[i + 1:]:
            if a[1] < b[2] and b[1] < a[2] and a[3] < b[4] and b[3] < a[4]:
                bad.append((a[0], b[0], round(max(a[1], b[1]), 2)))
    return bad


def _tok(t):
    return [x for x in re.sub(r"[^a-z0-9*' ]", " ", (t or "").lower()).split() if x]


def drop_hook_words(words, hook, show):
    """The hook already shows the opening words: leave them out of the captions so the first caption
    line never repeats the hook. Only the words at the very start that the hook contains are dropped."""
    ht = set(_tok(hook))
    if not ht:
        return list(words)
    i = 0
    while i < len(words) and words[i]["s"] < show and all(t in ht for t in _tok(words[i]["w"])):
        i += 1
    return list(words[i:])
