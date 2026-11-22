"""Caption verification: every spoken word is captioned, and every caption word is checked against a second source.

Base = large-v3 words on the final clip ({'w','s','e','p'}, clip clock). Second sources:
  gemini = text of Gemini's audio transcription of the same clip (no times),
  ocr    = [{'s','e','text'}] burned-in subtitles read from the video (game dialogue); preferred over Gemini
           for the lines that really are this speech.
Nothing is guessed: a word is changed only when a source heard something else AND the rule below says so;
a disagreement the rules cannot settle stays 'unresolved' and the render gate fails on it.
Pure logic (difflib + arithmetic), unit-tested without audio. Write out/captions_check.json with report_json()."""
import difflib
import re

FILLERS = set("um uh uhh umm hm hmm mm mhm erm er ah".split())
P_KEEP = 0.75          # a base word at least this sure is kept against a lone second source
NEAR = 0.8             # spelling-level difference (Lusia/Lucia, plural): the base spelling stands
OCR_MIN_LINE = 0.5     # an OCR line counts as this speech only when this share of its words line up
MIN_WORD_S = 0.10      # shortest slot for a word that has to be placed into a gap
MIN_COVERAGE = 0.90
PAD = 0.10             # tolerance when counting speech seconds that a caption covers


def tok(text):
    """Comparison form of a word: lower case, edge punctuation removed (the * of a masked curse stays)."""
    return re.sub(r"^[^\w*']+|[^\w*']+$", "", (text or "").lower())


def same(a, b):
    if a == b:
        return True
    if "*" in a or "*" in b:        # f*** vs fuck: a run of stars matches one or more letters
        star, other = (a, b) if "*" in a else (b, a)
        pat = "^" + re.sub(r"(\\\*)+", ".+", re.escape(star)) + "$"
        return bool(re.match(pat, other))
    return False


def near(a, b):
    if not a or not b:
        return False
    if same(a, b):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= NEAR or a.rstrip("s") == b.rstrip("s")


def src_tokens(text):
    out = []
    for t in (text or "").split():
        c = tok(t)
        if c and c not in FILLERS:
            out.append(c)
    return out


def case_map(texts):
    """comparison token -> the spelling the source used (names keep their capital letters)."""
    m = {}
    for t in texts:
        for x in (t or "").split():
            c = tok(x)
            if c and c not in m:
                m[c] = re.sub(r"^[^\w*']+|[^\w*']+$", "", x)
    return m


def align(base, ref):
    """base, ref: lists of comparison tokens. Returns (kinds, inserts):
    kinds[i] = ('match', tok) | ('diff', tok) | ('absent', None) for base word i;
    inserts[i] = tokens the source has that sit AFTER base word i (-1 = before the first word)."""
    kinds = [("absent", None)] * len(base)
    inserts = {}
    sm = difflib.SequenceMatcher(None, base, ref, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                kinds[i1 + k] = ("match", ref[j1 + k])
        elif tag == "replace":
            used, last = set(), j1 - 1
            if i2 - i1 == j2 - j1:          # one-for-one swap: a substitution, however different the words look
                for k in range(i2 - i1):
                    kinds[i1 + k] = ("match" if same(base[i1 + k], ref[j1 + k]) else "diff", ref[j1 + k])
                continue
            for i in range(i1, i2):
                best, bj = 0.0, None
                for j in range(last + 1, j2):
                    r = 1.0 if same(base[i], ref[j]) else difflib.SequenceMatcher(None, base[i], ref[j]).ratio()
                    if r > best:
                        best, bj = r, j
                if bj is not None and best >= 0.5:
                    kinds[i] = ("match" if same(base[i], ref[bj]) else "diff", ref[bj])
                    used.add(bj)
                    last = bj
            left = [ref[j] for j in range(j1, j2) if j not in used]
            if left:
                inserts.setdefault(i2 - 1, []).extend(left)
        elif tag == "insert":
            inserts.setdefault(i1 - 1, []).extend(ref[j1:j2])
    return kinds, inserts


def _mid(w):
    return (w["s"] + w["e"]) / 2


def ocr_votes(base, ocr_lines):
    """Per base word the OCR verdict from the line on screen at that moment, only for lines that are this speech."""
    kinds = [("absent", None)] * len(base)
    inserts, used = {}, []
    toks = [tok(w["w"]) for w in base]
    for ln in ocr_lines or []:
        lt = src_tokens(ln.get("text", ""))
        idx = [i for i, w in enumerate(base) if ln["s"] - 0.4 <= _mid(w) <= ln["e"] + 0.4 and toks[i]]
        if not lt or not idx:
            continue
        k, ins = align([toks[i] for i in idx], lt)
        hit = sum(1 for x in k if x[0] == "match") / max(len(lt), 1)
        if hit < OCR_MIN_LINE:
            continue
        used.append({"s": ln["s"], "e": ln["e"], "text": ln.get("text", ""), "match": round(hit, 2)})
        for n, i in enumerate(idx):
            if k[n][0] != "absent":
                kinds[i] = k[n]
        for n, v in ins.items():
            at = idx[n] if n >= 0 else idx[0] - 1
            inserts.setdefault(at, []).extend(v)
    return kinds, inserts, used


def _replace_core(old, new):
    m = re.match(r"^([^\w*']*)(.*?)([^\w*']*)$", old)
    return m.group(1) + new + m.group(3) if m else new


def speech_overlap(a, b, spans):
    return sum(max(0.0, min(b, e) - max(a, s)) for s, e in spans)


def coverage(words, spans, pad=PAD):
    """Share of the speech seconds (VAD spans) that a caption word is on screen for."""
    total = sum(e - s for s, e in spans)
    if total <= 0:
        return 1.0, 0.0, 0.0
    iv = sorted((w["s"] - pad, w["e"] + pad) for w in words)
    merged = []
    for s, e in iv:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    cov = sum(speech_overlap(s, e, spans) for s, e in merged)
    return min(cov / total, 1.0), total, cov


def verify(base, gemini_text=None, ocr_lines=None, spans=None, gemini_error=None):
    """-> (final_words, check). base: [{'w','s','e','p'}] on the clip clock, unmasked."""
    spans = spans or []
    base = [dict(w) for w in base]
    toks = [tok(w["w"]) for w in base]
    g_on = gemini_text is not None and bool(gemini_text.strip())
    gk, gi = align(toks, src_tokens(gemini_text)) if g_on else ([("n/a", None)] * len(base), {})
    ok_, oi, o_used = ocr_votes(base, ocr_lines) if ocr_lines else ([("n/a", None)] * len(base), {}, [])
    o_on = bool(ocr_lines) and bool(o_used)
    cases = case_map([gemini_text or ""])
    cases.update(case_map([ln.get("text", "") for ln in (ocr_lines or [])]))
    rows, out, unresolved, corrections = [], [], [], []
    for i, w in enumerate(base):
        p = w.get("p", 1.0)
        g, o = gk[i], ok_[i]
        final, status, src, why = w["w"], "agreed", "base", ""
        if g[0] == "match" or o[0] == "match":
            status = "agreed"
            if g[0] == "diff" and o[0] != "diff":
                why = "gemini differs, ocr/base stand"
            if o[0] == "diff":
                final, status, src = _replace_core(w["w"], cases.get(o[1], o[1])), "corrected", "ocr"
                why = "ocr line (game dialogue) differs from base"
        elif o[0] == "diff":
            final, status, src = _replace_core(w["w"], cases.get(o[1], o[1])), "corrected", "ocr"
            why = "ocr line (game dialogue) differs from base"
        elif g[0] == "diff":
            if near(toks[i], g[1]):
                status, why = "agreed", "spelling-level difference, base kept"
            elif p < P_KEEP:
                final, status, src = _replace_core(w["w"], cases.get(g[1], g[1])), "corrected", "gemini"
                why = f"base unsure (p={p:.2f}), gemini heard {g[1]!r}"
            else:
                status, why = "kept_confident", f"base sure (p={p:.2f}), gemini heard {g[1]!r}"
        elif g[0] == "n/a" and o[0] == "n/a":
            status = "single_source"
        elif g[0] == "absent" and p < 0.3 and (w["e"] - w["s"]) < 0.25 and not toks[i].isalpha():
            status, why = "single_source", "no second source heard it"
        else:
            status = "single_source"
        if status == "corrected":
            corrections.append({"i": i, "from": w["w"], "to": final, "source": src, "why": why, "t": round(w["s"], 2)})
        nw = {**w, "w": final}
        out.append(nw)
        rows.append({"i": i, "base": w["w"], "final": final, "s": round(w["s"], 3), "e": round(w["e"], 3),
                     "p": round(p, 3), "gemini": g[0], "ocr": o[0], "status": status, "source": src, "why": why})
    # two second sources that hear different things from the base AND from each other: nothing settles it
    for i, r in enumerate(rows):
        if gk[i][0] == "diff" and ok_[i][0] == "diff" and not same(gk[i][1], ok_[i][1]) and not near(gk[i][1], ok_[i][1]):
            r["status"], r["why"] = "unresolved", f"base {r['base']!r}, gemini {gk[i][1]!r}, ocr {ok_[i][1]!r}"
            unresolved.append({"i": i, "t": r["s"], "why": r["why"]})
    # missing words: tokens a source has between two base words
    ins_all = {}
    for src, ins in (("gemini", gi), ("ocr", oi)):
        for at, tl in ins.items():
            ins_all.setdefault(at, {}).setdefault(src, []).extend(tl)
    added, rejected = [], []
    for at in sorted(ins_all):
        srcs = ins_all[at]
        tl = srcs.get("ocr") or srcs.get("gemini")
        who = "ocr" if srcs.get("ocr") else "gemini"
        both = bool(srcs.get("ocr") and srcs.get("gemini"))
        g0 = out[at]["e"] if at >= 0 else 0.0
        g1 = out[at + 1]["s"] if at + 1 < len(out) else (max((w["e"] for w in out), default=0.0) + 1.0)
        chunks = [(max(s, g0), min(e, g1)) for s, e in spans if min(e, g1) - max(s, g0) > 0.02]
        if not chunks:
            rejected.append({"after": at, "words": tl, "source": who, "why": "no speech in the gap"})
            continue
        a, b = chunks[0][0], chunks[-1][1]
        need = MIN_WORD_S * len(tl)
        if b - a < need:
            if b - a < 0.04:
                rejected.append({"after": at, "words": tl, "source": who, "why": "gap too short"})
            else:
                unresolved.append({"i": at, "t": round(g0, 2), "why": f"{who} has {tl} but the speech gap is {b - a:.2f} s"})
            continue
        step = (b - a) / len(tl)
        for k, t in enumerate(tl):
            added.append((at, {"w": cases.get(t, t), "s": round(a + k * step, 3), "e": round(a + (k + 1) * step, 3), "p": 0.5,
                               "inserted": who + ("+gemini" if both else "")}))
    final = []
    adds_by = {}
    for at, nw in added:
        adds_by.setdefault(at, []).append(nw)
    final.extend(adds_by.get(-1, []))
    for i, w in enumerate(out):
        final.append(w)
        final.extend(adds_by.get(i, []))
    cov, total, covered = coverage(final, spans)
    reasons = []
    if cov < MIN_COVERAGE:
        reasons.append(f"captions cover {cov * 100:.0f}% of the speech seconds (need {MIN_COVERAGE * 100:.0f}%)")
    if unresolved:
        reasons.append(f"{len(unresolved)} unresolved disagreement(s): " + "; ".join(u["why"] for u in unresolved[:3]))
    check = {"coverage": round(cov, 3), "speech_sec": round(total, 2), "covered_sec": round(covered, 2),
             "sources": {"base": {"words": len(base)},
                         "gemini": {"available": g_on, "words": len(src_tokens(gemini_text or "")), "error": gemini_error},
                         "ocr": {"available": o_on, "lines_used": o_used}},
             "second_source": bool(g_on or o_on),
             "counts": {"agreed": sum(r["status"] == "agreed" for r in rows),
                        "single_source": sum(r["status"] == "single_source" for r in rows),
                        "corrected": len(corrections), "kept_confident": sum(r["status"] == "kept_confident" for r in rows),
                        "inserted": len(added), "unresolved": len(unresolved), "rejected_inserts": len(rejected)},
             "corrections": corrections, "unresolved": unresolved, "rejected_inserts": rejected,
             "inserted": [{"after": at, **nw} for at, nw in added], "words": rows,
             "gate": {"ok": not reasons, "reasons": reasons}}
    return final, check
