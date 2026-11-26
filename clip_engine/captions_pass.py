"""The caption check as one step of render(): rescue ASR + Gemini audio text + OCR of game subtitles -> captions_verify.

run() takes the clip-clock words (unmasked), returns (final unmasked words, check dict). The caller masks profanity
afterwards (asr_fix.mask_profanity), writes out/captions_check.json (write_json) and refuses the clip when
check['gate']['ok'] is false (coverage < 90% of the speech seconds, or a disagreement nothing settles)."""
import json
import os
import re

from . import caption_sources as cs
from . import captions_verify as cv
from . import quiet_rescue as qr
from . import speech_spans as ss
from . import transcribe
from . import word_retime as wr


def drop_contradicted(words, ocr, min_p=0.45):
    """A low-confidence ASR word that the on-screen subtitle contradicts is a hallucination: drop it
    (it would otherwise be shown, even highlighted). Words the subtitle confirms, or that no subtitle covers, stay."""
    def toks(t):
        return set(re.findall(r"[a-z0-9']+", (t or "").lower()))
    out = []
    for w in words:
        if (w.get("p", 1.0) or 0.0) < min_p:
            mid = (w["s"] + w["e"]) / 2.0
            near = [ln for ln in (ocr or []) if ln["s"] - 0.5 <= mid <= ln["e"] + 0.5]
            if near:
                seen = set().union(*[toks(ln["text"]) for ln in near])
                if re.sub(r"[^a-z0-9']", "", w["w"].lower()) not in seen:
                    continue
        out.append(w)
    return out


def summary(check):
    """The small block that goes into render.json grade_inputs (the grader reads this, not the per-word rows)."""
    src = check.get("sources", {})
    return {"coverage": check.get("coverage"), "speech_sec": check.get("speech_sec"), "covered_sec": check.get("covered_sec"),
            "second_source": check.get("second_source"), "counts": check.get("counts"),
            "unresolved": check.get("unresolved", []), "gate": check.get("gate"),
            "speech": {k: check.get("speech", {}).get(k) for k in ("vad_speech_sec", "counted_speech_sec", "excluded_sec",
                                                                    "excluded", "raw_window_coverage")},
            "gemini": {k: src.get("gemini", {}).get(k) for k in ("available", "words", "error")},
            "ocr": {"available": src.get("ocr", {}).get("available"),
                    "lines_used": len(src.get("ocr", {}).get("lines_used", [])),
                    "lines_read": check.get("ocr_info", {}).get("lines"), "error": check.get("ocr_info", {}).get("error")},
            "rescue": {k: check.get("rescue", {}).get(k) for k in ("chunk_sec", "added", "error")}}


def write_json(check, out_dir):
    with open(os.path.join(out_dir, "captions_check.json"), "w") as f:
        json.dump(check, f, indent=1)


def run(media, s_f, dur, words, src_w, src_h, model_fn=None, avoid_px=None, key=None, log=print,
        pcm=None, spans=None, gemini_fn=None, ocr_fn=None, ocr_frames=None):
    """words: clip-clock {'w','s','e','p'}, unmasked. model_fn() -> transcribe_fn for the rescue pass (None = no rescue).
    pcm/spans/gemini_fn/ocr_fn/ocr_frames are injectable for tests."""
    if pcm is None:
        pcm = transcribe.decode_pcm(media, s_f, s_f + dur)
    if spans is None:
        spans, _ = transcribe.vad_stats(pcm)
    base = sorted((dict(w) for w in words), key=lambda w: w["s"])
    rescued, rinfo = [], {"skipped": "no rescue model"}
    retime_info = {"skipped": "no model"}
    quiet_info = {"skipped": "no model"}
    if model_fn is not None:
        box = []

        def lazy(audio):            # the model is loaded only when some speech really has no word
            if not box:
                box.append(model_fn())
            return box[0](audio)
        try:        # same words, true times: a second whole-window pass with the VAD off (see word_retime.py for the fixed rules)
            heard = lazy(pcm)
            base, moves = wr.retime(base, heard, spans)
            retime_info = {"heard_words": len(heard), "moved": len(moves), "moves": moves}
            log(f"caption retime: {len(moves)} word(s) moved to where the VAD-off pass hears them")
        except Exception as e:
            retime_info = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
            log("caption retime failed: " + retime_info["error"])
        rescued, rinfo = cs.rescue(pcm, base, spans, lazy)
        log(f"caption rescue: {rinfo['chunk_sec']} s of speech without a word, {rinfo['added']} word(s) added")
        try:        # quiet speech under loud sound: each still-uncovered stretch alone, high-passed + normalised, two passes must agree
            left = cs.uncovered(base + rescued, spans)
            qw, quiet_info = qr.run(pcm, left, base + rescued, lazy)
            rescued = rescued + qw
            log(f"caption quiet rescue: {len(left)} stretch(es) still uncovered, {len(qw)} word(s) added")
        except Exception as e:
            quiet_info = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
            log("caption quiet rescue failed: " + quiet_info["error"])
    base = sorted(base + rescued, key=lambda w: w["s"])
    for w in base:
        w["e"] = min(w["e"], dur)
    if gemini_fn or key:
        gtext, gerr = (gemini_fn or cs.gemini_text)(pcm, key)
    else:
        gtext, gerr = None, "GEMINI_API_KEY not set"
    log("caption source gemini: " + (f"{len(cv.src_tokens(gtext))} words" if gtext else f"unavailable ({str(gerr)[:60]})"))
    ocr, oinfo = cs.ocr_lines(media, s_f, dur, src_w, src_h, avoid_px, frames=ocr_frames, ocr=ocr_fn)
    log("caption source ocr: " + (f"{oinfo['lines']} subtitle lines from {oinfo['frames']} frames" if oinfo["available"]
                                 else f"unavailable ({oinfo.get('error')})"))
    # speech seconds = the VAD seconds minus digital silence and word-less non-speech sound (every stretch is logged)
    spans_eff, _, sinfo = ss.refine(pcm, spans, base)
    log(f"caption speech seconds: {sinfo['counted_speech_sec']} of {sinfo['vad_speech_sec']} s VAD, "
        f"{len(sinfo['excluded'])} stretch(es) excluded ({sinfo['excluded_sec']} s)")
    final, check = cv.verify(base, gtext, ocr, spans_eff, gerr)
    final = drop_contradicted(final, ocr)
    for w in final:
        w["e"] = min(w["e"], dur)
    raw = ss.raw_coverage(final, dur)
    sinfo["raw_window_coverage"] = round(raw, 3)
    check["speech"] = sinfo
    check["coverage_vad_unrefined"] = round(cv.coverage(final, spans)[0], 3)
    if raw < ss.MIN_RAW_COVERAGE:                 # backstop: the exclusions cannot hide a clip that is mostly uncaptioned
        check["gate"]["reasons"].append(f"captions cover only {raw * 100:.0f}% of the whole clip window with no "
                                        f"exclusions (need {ss.MIN_RAW_COVERAGE * 100:.0f}%)")
        check["gate"]["ok"] = False
    check["retime"] = retime_info
    check["quiet_rescue"] = quiet_info
    check["rescue"], check["ocr_info"], check["ocr_lines"] = rinfo, oinfo, ocr
    check["rescued_words"] = [{"w": w["w"], "s": w["s"], "e": w["e"], "p": w["p"]} for w in rescued]
    log(f"captions check: coverage {check['coverage'] * 100:.0f}% of {check['speech_sec']} s speech, "
        f"{check['counts']['corrected']} corrected, {check['counts']['inserted']} inserted, "
        f"{check['counts']['unresolved']} unresolved, gate {'PASS' if check['gate']['ok'] else 'FAIL'}")
    return final, check
