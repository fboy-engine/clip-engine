"""Evidence for every speech stretch that no caption word covers (out/captions_gaps.json + out/gaps/*.wav).

MEASUREMENT ONLY: it changes no word, no speech span and no gate. It runs after a failed caption gate so the numbers
for the next fix come from the audio, not from a guess. Per stretch it records:
  - level and voice-likeness (pitch share, syllable rhythm, speech-band share, flatness),
  - the caption words and the burned-in subtitle lines around it,
  - what large-v3 hears there with VAD off under several settings (int8 / float32, narrow / wide context),
  - what the caption coverage WOULD be if those words were taken (projection only, nothing is applied),
and for the whole clip how many caption words have a collapsed (< 0.06 s) or smeared (> 1 s) time slot.
One mixed audio channel cannot say WHO is speaking, so the hint never names a speaker: listen to gaps/<n>.wav.
"""
import json
import os
import time
import wave

import numpy as np

from . import caption_sources as cs
from . import captions_verify as cv
from . import speech_spans as ss
from . import transcribe

SR = transcribe.SR
MIN_LEN = 0.10          # shortest stretch listed (the rescue pass only looks at 0.30 s and up; the gate counts every second)
JOIN = 0.40
KEEP_CTX = 0.50         # audio kept around a stretch for listening
COLLAPSED_S = 0.06      # a spoken word takes at least ~0.1 s: shorter is a timestamp collapse
STRETCHED_S = 1.00      # one word longer than this is a timestamp smear
HEARD_MIN_P = cs.RESCUE_MIN_P
MAX_STRETCHES = 12      # variants run on the longest ones


def _num(v, nd=2):
    return None if v is None else round(float(v), nd)


def voiced_share(x, sr=SR):
    """(share of voiced frames, frames judged): 40 ms frames, 20 ms hop, louder than -50 dBFS, clear pitch
    (normalised autocorrelation >= 0.5 between 70 and 400 Hz). (None, 0) when no frame is loud enough."""
    n, hop = int(0.040 * sr), int(0.020 * sr)
    lo, hi = int(sr / 400), int(sr / 70)
    voiced = total = 0
    for i in range(0, len(x) - n + 1, hop):
        f = np.asarray(x[i:i + n], dtype=np.float64)
        if 10 * np.log10(max(float((f ** 2).mean()), 1e-12)) < -50.0:
            continue
        total += 1
        f = f - f.mean()
        ac = np.fft.irfft(np.abs(np.fft.rfft(f, 2 * n)) ** 2)[:n]
        if ac[0] > 0 and ac[lo:hi + 1].max() / ac[0] >= 0.5:
            voiced += 1
    return (voiced / total if total else None), total


def spectral_stats(x, sr=SR):
    """(speech-band 300-3400 Hz share of the power above 50 Hz, spectral flatness 100-8000 Hz: 1 = noise, 0 = tonal)."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) < 64:
        return None, None
    sp = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    tot = sp[f >= 50].sum()
    if tot <= 0:
        return None, None
    band = sp[(f >= 300) & (f <= 3400)].sum() / tot
    m = sp[(f >= 100) & (f <= 8000)] + 1e-20
    return float(band), float(np.exp(np.log(m).mean()) / m.mean())


def features(x, sr=SR):
    x = np.asarray(x, dtype=np.float64)
    if len(x) < int(0.05 * sr):
        return {"too_short": True}
    lv = ss.frame_db(x, sr)
    vs, nfr = voiced_share(x, sr)
    band, flat = spectral_stats(x, sr)
    return {"mean_dbfs": _num(20 * np.log10(max(float(np.sqrt((x ** 2).mean())), 1e-9)), 1),
            "peak_dbfs": _num(20 * np.log10(max(float(np.abs(x).max()), 1e-9)), 1),
            "silent_frame_share": _num((lv < ss.SILENCE_DB).mean() if len(lv) else None),
            "syllable_modulation": _num(ss.syllable_modulation(x, sr)),
            "voiced_frame_share": _num(vs), "loud_frames": nfr,
            "speech_band_share": _num(band), "flatness": _num(flat)}


def timing_quality(words):
    """How many caption words have a slot too short or too long to be a real word time."""
    n = len(words)
    col = [w for w in words if w["e"] - w["s"] < COLLAPSED_S]
    sme = [w for w in words if w["e"] - w["s"] > STRETCHED_S]
    ex = lambda ws: [{"w": w["w"], "s": round(w["s"], 2), "e": round(w["e"], 2)} for w in ws[:12]]
    return {"words": n, "collapsed": len(col), "collapsed_share": _num(len(col) / n if n else 0.0, 3),
            "smeared": len(sme), "collapsed_examples": ex(col), "smeared_examples": ex(sme),
            "collapsed_s": COLLAPSED_S, "smeared_s": STRETCHED_S}


def _overlap(a, b, c, d):
    return max(0.0, min(b, d) - max(a, c))


def words_near(words, a, b, tol):
    return [{"w": w["w"], "s": round(w["s"], 2), "e": round(w["e"], 2), "p": _num(w.get("p"), 2)}
            for w in sorted(words, key=lambda w: w["s"]) if _overlap(a - tol, b + tol, w["s"], w["e"]) > 0
            or (a - tol <= w["s"] <= b + tol)]


def inside_words(heard_words, a, b):
    """Words of one variant whose middle lies in the stretch, with the p filter the rescue uses."""
    return [w for w in heard_words if a - 0.05 <= (w["s"] + w["e"]) / 2 <= b + 0.05]


def hint(feat, heard_inside, counted_sec):
    """What the measurements support. Never a speaker; never a gate decision."""
    if feat.get("too_short"):
        return "too short to measure", ["stretch under 0.05 s"]
    why = []
    if (feat.get("silent_frame_share") or 0) >= 0.8:
        return "digital silence", [f"{feat['silent_frame_share'] * 100:.0f}% of frames under {ss.SILENCE_DB:g} dBFS"]
    good = [w for w in heard_inside if (w.get("p") or 0) >= HEARD_MIN_P]
    if good:
        why.append("large-v3 with VAD off heard: " + " ".join(w["w"] for w in good))
        return "words heard here that the base pass left without a usable time slot", why
    vs, mod = feat.get("voiced_frame_share"), feat.get("syllable_modulation")
    if vs is not None:
        why.append(f"voiced frames {vs * 100:.0f}%")
    if mod is not None:
        why.append(f"syllable rhythm {mod:.2f} (speech words measured 0.37-0.98)")
    if vs is not None and vs < 0.2:
        return "no pitched voice: music, crowd, engine or noise counted as speech by the VAD", why
    if vs is not None and vs >= 0.5:
        return "pitched voice, no variant heard a word: shout, laugh, mumble or another voice; listen to the wav", why
    return "mixed; the numbers do not settle it, listen to the wav", why


def write_wav(path, pcm, sr=SR):
    a = np.clip(np.asarray(pcm, dtype=np.float32), -1.0, 1.0)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((a * 32767).astype(np.int16).tobytes())


def heard(model, pcm, a, b, ctx, beam=5, language="en", sr=SR):
    """Re-hear [a-ctx, b+ctx] with VAD off and the thresholds that drop quiet speech relaxed (the rescue's settings)
    -> [{'w','s','e','p'}] on the clip clock."""
    s0, s1 = max(0.0, a - ctx), min(len(pcm) / sr, b + ctx)
    segs, _ = model.transcribe(pcm[int(s0 * sr):int(s1 * sr)], language=language, beam_size=beam, word_timestamps=True,
                               vad_filter=False, condition_on_previous_text=False,
                               no_speech_threshold=None, log_prob_threshold=None)
    return [{"w": w["w"], "s": round(w["s"] + s0, 3), "e": round(w["e"] + s0, 3), "p": w["p"]}
            for seg in transcribe.collect(segs) for w in seg["words"]]


def projected(words, spans_eff, heard_words, stretch_list):
    """Coverage the gate WOULD report if this variant's words (p >= HEARD_MIN_P, middle inside a stretch, on no
    existing word) were taken. Projection only."""
    add = []
    for w in heard_words:
        mid = (w["s"] + w["e"]) / 2
        if (w.get("p") or 0) < HEARD_MIN_P or not any(a - 0.05 <= mid <= b + 0.05 for a, b in stretch_list):
            continue
        if cs._inside_word(mid, words + add):
            continue
        add.append(w)
    cov = cv.coverage(words + add, spans_eff)[0]
    return {"words_added": len(add), "coverage": _num(cov, 3), "added": [w["w"] for w in add]}


def load_model(name, cache_dir, compute_type):
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    path = download_model(name, cache_dir=cache_dir)
    return WhisperModel(path, device="cpu", compute_type=compute_type, cpu_threads=os.cpu_count() or 2)


# variant name -> (compute type, context seconds around the stretch; None = the whole clip window in one pass).
# Order matters: the cheap int8 passes run first, float32 only when time is left.
VARIANTS = {"int8_window": ("int8", None), "int8_ctx1.5": ("int8", 1.5), "int8_ctx0.35": ("int8", 0.35),
            "float32_ctx1.5": ("float32", 1.5)}
BUDGET_S = 300.0              # the whole evidence pass; the render step has a 35 min cap and must still finish
FLOAT32_MIN_LEFT_S = 150.0    # a float32 large-v3 only starts when this much of the budget is left
FLOAT32_STRETCHES = 5


def diagnose(pcm, spans, words, gate, ocr_lines, out_dir, model_name="large-v3", cache_dir=None, log=print,
             loader=load_model, variants=None, sr=SR, budget_s=BUDGET_S, clock=time.monotonic):
    """-> report dict (also written to out_dir/captions_gaps.json after every stage; listening wavs in out_dir/gaps/).
    words = every caption word the gate counted (rescue and inserted words included); spans = raw VAD spans.
    The audio measurements are written first; each Whisper variant is added as it finishes; a variant that does not
    fit the time budget is listed in 'skipped_for_time', never silently dropped."""
    t0 = clock()

    def left():
        return budget_s - (clock() - t0)
    dur = len(pcm) / sr
    words = sorted((dict(w) for w in words), key=lambda w: w["s"])
    st = cs.uncovered(words, spans, cv.PAD, MIN_LEN, JOIN)
    spans_eff, excluded, sinfo = ss.refine(pcm, spans, words)
    order = sorted(range(len(st)), key=lambda i: -(st[i][1] - st[i][0]))[:MAX_STRETCHES]
    names = variants or VARIANTS
    heard_by = {k: {} for k in names}
    window_words, errors, skipped, ran = {}, {}, [], set()
    os.makedirs(os.path.join(out_dir, "gaps"), exist_ok=True)
    base_rows = []
    for i, (a, b) in enumerate(st):
        n = i + 1
        x0, x1 = max(0.0, a - KEEP_CTX), min(dur, b + KEEP_CTX)
        fn = f"{n:02d}_{a:.2f}-{b:.2f}.wav"
        write_wav(os.path.join(out_dir, "gaps", fn), pcm[int(x0 * sr):int(x1 * sr)], sr)
        base_rows.append({"n": n, "start": round(a, 2), "end": round(b, 2), "sec": round(b - a, 2),
                          "excluded_sec": round(sum(_overlap(a, b, e["start"], e["end"]) for e in excluded), 2),
                          "wav": f"gaps/{fn} (audio {x0:.2f}-{x1:.2f} s)",
                          "caption_words_near": words_near(words, a, b, 0.6),
                          "subtitle_lines_on_screen": [l["text"] for l in (ocr_lines or [])
                                                       if _overlap(a, b, l["s"], l["e"]) > 0],
                          "features": features(pcm[int(a * sr):int(b * sr)], sr)})
    tq = timing_quality(words)

    def build(stage):
        rows = []
        for i, rec in enumerate(base_rows):
            r = dict(rec)
            if i in order:
                r["heard"] = {k: heard_by[k][i] for k in names if i in heard_by[k]}
                allh = [w for k in names for w in heard_by[k].get(i, [])]
                r["hint"], r["hint_why"] = hint(r["features"], allh, r["sec"] - r["excluded_sec"])
            else:
                r["hint"], r["hint_why"] = "not measured (outside the 12 longest stretches)", []
            rows.append(r)
        proj = {}
        for k in names:
            if k not in ran:
                continue
            hw = window_words.get(k)
            if hw is None:
                hw = [w for i in order for w in heard_by[k].get(i, [])]
            proj[k] = projected(words, spans_eff, hw, st)
        rep = {"stage": stage, "elapsed_s": round(clock() - t0, 1), "budget_s": budget_s, "clip_sec": round(dur, 2),
               "vad_speech_sec": sinfo["vad_speech_sec"], "counted_speech_sec": sinfo["counted_speech_sec"],
               "gate": gate, "stretch_count": len(st), "stretch_sec": round(sum(b - a for a, b in st), 2),
               "timing_quality": tq, "stretches": rows, "projected_coverage_if_taken": proj,
               "variant_errors": errors, "skipped_for_time": skipped,
               "note": "measurement only: nothing here is applied. One mixed channel cannot say who speaks; "
                       "listen to the wavs."}
        with open(os.path.join(out_dir, "captions_gaps.json"), "w") as f:
            json.dump(rep, f, indent=1)
        return rep

    rep = build("audio measurements only")
    by_type = {}
    for k, (ct, _) in names.items():
        by_type.setdefault(ct, []).append(k)
    for ctype, ks in by_type.items():
        if ctype != "int8" and left() < FLOAT32_MIN_LEFT_S:
            skipped.append(f"{', '.join(ks)}: {left():.0f} s of the {budget_s:g} s budget left, a {ctype} model "
                           f"needs {FLOAT32_MIN_LEFT_S:g} s")
            continue
        if left() <= 0:
            skipped.append(f"{', '.join(ks)}: budget used up")
            continue
        try:
            model = loader(model_name, cache_dir, ctype)
        except Exception as e:                  # a variant that cannot load is reported, not fatal
            for k in ks:
                errors[k] = f"load {ctype}: {type(e).__name__}: {str(e)[:120]}"
            continue
        for k in ks:
            ctx = names[k][1]
            idx = order if ctype == "int8" else order[:FLOAT32_STRETCHES]
            try:
                if ctx is None:
                    if left() <= 0:
                        skipped.append(f"{k}: budget used up")
                        continue
                    window_words[k] = heard(model, pcm, 0.0, dur, 0.0)
                    for i in idx:
                        heard_by[k][i] = inside_words(window_words[k], *st[i])
                else:
                    for i in idx:
                        if left() <= 0:
                            skipped.append(f"{k}: stretch {i + 1} and later not run, budget used up")
                            break
                        a, b = st[i]
                        heard_by[k][i] = inside_words(heard(model, pcm, a, b, ctx), a, b)
                ran.add(k)
            except Exception as e:
                errors[k] = f"{type(e).__name__}: {str(e)[:120]}"
            rep = build(f"after {k}")
        del model
    rep = build("complete" if not (skipped or errors) else "partial")
    log(f"caption gaps: {len(st)} stretch(es), {rep['stretch_sec']} s, {rep['stage']}, {rep['elapsed_s']} s; "
        "wrote captions_gaps.json")
    return rep


def diagnose_failed(media, s_f, dur, words, cap_check, model_name, cache_dir, out_dir, log=print):
    """Called by render() when the caption gate failed. Never raises (the gate error is the result)."""
    try:
        pcm = transcribe.decode_pcm(media, s_f, s_f + dur)
        spans, _ = transcribe.vad_stats(pcm)
        return diagnose(pcm, spans, words, cap_check.get("gate"), cap_check.get("ocr_lines"), out_dir,
                        model_name=model_name, cache_dir=cache_dir, log=log)
    except Exception as e:
        log(f"caption gap evidence failed: {type(e).__name__}: {str(e)[:160]}")
        return None
