"""Quiet speech under loud game audio: re-hear each still-uncovered speech stretch on its own.

Rules fixed BEFORE any result was looked at, the same for every clip (nothing is tuned per clip, no gate is touched):
  * the stretch is padded by PAD_S on each side, high-passed (HP_HZ variants) and loudness-normalised to TARGET_DB RMS;
  * every variant is one pass of the same model with the VAD off;
  * a word is ADDED only when at least MIN_PASSES passes heard the same word (comparison form), each with p >= MIN_P,
    with midpoints within AGREE_S of each other, the word's middle inside the stretch, on no existing caption word;
  * its time is the earliest of the agreeing passes' times (the audio's own clock, offset back onto the clip clock).
Nothing is excluded and nothing is guessed from a phrase or a speaker. Every heard word, taken or not, is reported.
Pure numpy + injected transcribe_fn, unit-tested on synthetic audio and fake transcribers."""
import numpy as np

from . import captions_verify as cv

SR = 16000
PAD_S = 0.5
HP_HZ = (200.0, 300.0)
TARGET_DB = -20.0
MIN_P = 0.5
MIN_PASSES = 2
AGREE_S = 0.30
ORDER = 4
MAX_GAIN_DB = 30.0


def highpass(x, hz, sr=SR, order=ORDER):
    """Zero-phase Butterworth-shaped high-pass applied in the frequency domain."""
    x = np.asarray(x, dtype=np.float64)
    if len(x) == 0:
        return x
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    with np.errstate(divide="ignore"):
        h = 1.0 / np.sqrt(1.0 + (hz / np.maximum(f, 1e-9)) ** (2 * order))
    h[0] = 0.0
    return np.fft.irfft(X * h, n=len(x))


def normalise(x, target_db=TARGET_DB, max_gain_db=MAX_GAIN_DB):
    """RMS to target_db (gain capped), then a peak guard at 0.95. Silence stays silence."""
    x = np.asarray(x, dtype=np.float64)
    rms = float(np.sqrt((x ** 2).mean())) if len(x) else 0.0
    if rms < 1e-7:
        return x.astype(np.float32)
    gain_db = min(target_db - 20 * np.log10(rms), max_gain_db)
    y = x * 10 ** (gain_db / 20)
    pk = float(np.abs(y).max())
    if pk > 0.95:
        y *= 0.95 / pk
    return y.astype(np.float32)


def prepare(x, hz, sr=SR):
    return normalise(highpass(x, hz, sr))


def agree(passes, a, b, existing, min_p=MIN_P, min_passes=MIN_PASSES, agree_s=AGREE_S):
    """passes: {name: [{'w','s','e','p'}]} on the clip clock. -> (words to add, report rows of every heard word)."""
    rows, out = [], []
    names = list(passes)
    if not names:
        return out, rows
    base = passes[names[0]]
    claimed = set()
    for w in sorted((w for n in names for w in passes[n]), key=lambda w: w["s"]):
        tk = cv.tok(w["w"])
        mid = (w["s"] + w["e"]) / 2
        if not tk or (w["w"], w["s"]) in claimed:
            continue
        group = {}
        for n in names:
            for h in passes[n]:
                if cv.tok(h["w"]) == tk and abs((h["s"] + h["e"]) / 2 - mid) <= agree_s and h.get("p", 0.0) >= min_p:
                    if n not in group or h["p"] > group[n]["p"]:
                        group[n] = h
        for h in group.values():
            claimed.add((h["w"], h["s"]))
        claimed.add((w["w"], w["s"]))
        row = {"w": w["w"], "t": round(w["s"], 2), "p": round(w.get("p", 0.0), 2), "passes": sorted(group)}
        if len(group) < min_passes:
            row["taken"], row["why"] = False, f"heard sure (p>={min_p}) in {len(group)} pass(es), need {min_passes}"
        elif not (a - 0.05 <= mid <= b + 0.05):
            row["taken"], row["why"] = False, "outside the stretch"
        elif any(e["s"] - 0.05 <= mid <= e["e"] + 0.05 for e in existing + out):
            row["taken"], row["why"] = False, "on an existing word"
        else:
            hs = min(group.values(), key=lambda h: h["s"])
            he = max(h["e"] for h in group.values() if h["s"] <= hs["e"])
            out.append({"w": hs["w"], "s": round(hs["s"], 3), "e": round(max(he, hs["s"] + 0.1), 3),
                        "p": round(min(h["p"] for h in group.values()), 3), "rescued": True, "quiet": True})
            row["taken"], row["why"] = True, "agreed"
        rows.append(row)
    return sorted(out, key=lambda w: w["s"]), rows


def run(pcm, chunks, words, transcribe_fn, sr=SR, pad=PAD_S, hps=HP_HZ):
    """chunks: [(a, b)] uncovered speech stretches (clip clock). transcribe_fn(float32 audio) -> words relative to it."""
    n_total = len(pcm) / sr
    added, info = [], {"stretches": [], "rules": {"pad_s": pad, "hp_hz": list(hps), "min_p": MIN_P, "min_passes": MIN_PASSES,
                                                  "agree_s": AGREE_S, "target_db": TARGET_DB}}
    for a, b in chunks:
        s0, s1 = max(0.0, a - pad), min(n_total, b + pad)
        seg = np.asarray(pcm[int(s0 * sr):int(s1 * sr)])
        passes = {}
        for hz in hps:
            ws = transcribe_fn(prepare(seg, hz, sr))
            passes[f"hp{int(hz)}"] = [{**w, "s": w["s"] + s0, "e": w["e"] + s0} for w in ws]
        got, rows = agree(passes, a, b, list(words) + added)
        added += got
        info["stretches"].append({"a": round(a, 2), "b": round(b, 2), "heard": rows})
    info["added"] = len(added)
    return sorted(added, key=lambda w: w["s"]), info
