"""Which VAD seconds really are speech seconds for the caption gate.

Silero VAD (faster-whisper defaults) merges pauses shorter than 2 s and pads every span, so digital silence and
laughs / held sounds were counted as speech nobody could caption. refine() takes those seconds out of the denominator,
and says so for every stretch (captions_check.json -> speech.excluded). Two rules, nothing else:
  1. digital silence: frames under SILENCE_DB for at least SILENCE_MIN_S (measured level, not the VAD's opinion);
  2. a voiced stretch with NO caption word on it (the ASR incl. the rescue pass found none) whose sound is not
     speech-like: speech has a syllable rhythm (loudness moving 2-8 Hz), a laugh or a held sound is steady.
A stretch with a word on it (word +- PAD) is never excluded. The 90% rule on the remaining seconds is unchanged and
a backstop stays: the words must still cover MIN_RAW_COVERAGE of the whole clip with NO exclusions at all.
Pure numpy, unit-tested on synthetic audio."""
import numpy as np

from . import captions_verify as cv

SR = 16000
FRAME_S = 0.025
SILENCE_DB = -60.0         # dBFS per 25 ms frame; quiet speech in the real clips sits above -50, the room floor near -73
SILENCE_MIN_S = 0.20
NONWORD_MIN_S = 0.50       # shorter stretches cannot be judged, they stay counted
MOD_MAX = 0.35             # share of the loudness movement that is syllable rate (2-8 Hz); speech words measured 0.37-0.98
MIN_RAW_COVERAGE = 0.75
JOIN = 0.40


def frame_db(pcm, sr=SR, frame_s=FRAME_S):
    n = max(1, int(frame_s * sr))
    k = len(pcm) // n
    if k == 0:
        return np.zeros(0)
    x = np.asarray(pcm[:k * n], dtype=np.float64).reshape(k, n)
    return 20 * np.log10(np.maximum(np.sqrt((x ** 2).mean(axis=1)), 1e-9))


def digital_silence(pcm, sr=SR, db=SILENCE_DB, min_s=SILENCE_MIN_S, frame_s=FRAME_S):
    """[(a, b)] seconds where the level stays under `db` for at least `min_s`."""
    lv = frame_db(pcm, sr, frame_s)
    out, cur = [], None
    for k, v in enumerate(lv):
        t = k * frame_s
        if v < db:
            cur = [t, t + frame_s] if cur is None else [cur[0], t + frame_s]
        elif cur:
            out.append(tuple(cur))
            cur = None
    if cur:
        out.append(tuple(cur))
    return [(a, b) for a, b in out if b - a >= min_s]


def syllable_modulation(x, sr=SR):
    """Share of the loudness movement (log envelope, 25 ms steps) that lies at 2-8 Hz. None when too short to judge."""
    step = int(0.025 * sr)
    k = len(x) // step
    if k < int(NONWORD_MIN_S / 0.025):
        return None
    env = np.sqrt((np.asarray(x[:k * step], dtype=np.float64).reshape(k, step) ** 2).mean(axis=1)) + 1e-9
    le = np.log(env)
    le = le - le.mean()
    e = np.abs(np.fft.rfft(le)) ** 2
    f = np.fft.rfftfreq(len(le), 0.025)
    tot = e[f > 0.5].sum()
    return float(e[(f >= 2) & (f <= 8)].sum() / tot) if tot > 0 else 0.0


def subtract(spans, cuts):
    out = []
    for s, e in spans:
        segs = [(s, e)]
        for cs_, ce in cuts:
            nxt = []
            for x, y in segs:
                if ce <= x or cs_ >= y:
                    nxt.append((x, y))
                    continue
                if cs_ > x:
                    nxt.append((x, cs_))
                if ce < y:
                    nxt.append((ce, y))
            segs = nxt
        out += segs
    return [(a, b) for a, b in out if b - a > 1e-6]


def word_cover(words, pad=cv.PAD, lo=0.0, hi=None):
    iv = sorted((max(lo, w["s"] - pad), w["e"] + pad if hi is None else min(hi, w["e"] + pad)) for w in words)
    merged = []
    for s, e in iv:
        if e <= s:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(a, b) for a, b in merged]


def _intersect(spans, cuts):
    """The parts of `cuts` that lie inside `spans`."""
    return [(max(a, c), min(b, d)) for a, b in spans for c, d in cuts if min(b, d) - max(a, c) > 1e-6]


def _uncovered(spans, cover, join=JOIN):
    pieces = subtract(spans, cover)
    merged = []
    for a, b in sorted(pieces):
        if merged and a - merged[-1][1] <= join:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def raw_coverage(words, dur, pad=cv.PAD):
    """Share of the WHOLE clip window that a caption word is on screen for (no exclusions of any kind)."""
    if dur <= 0:
        return 1.0
    return min(1.0, sum(b - a for a, b in word_cover(words, pad, 0.0, dur)) / dur)


def refine(pcm, spans, words, sr=SR, pad=cv.PAD):
    """-> (spans without the excluded seconds, [{'start','end','sec','reason'}], info). `words` = every caption word,
    rescue words included: a stretch with a word on it is never excluded."""
    dur = len(pcm) / sr
    cover = word_cover(words, pad)
    excluded = []
    raw_sec = sum(e - s for s, e in spans)

    sil = [x for x in digital_silence(pcm, sr)]
    sil_free = subtract(_intersect(spans, sil), cover)        # inside the speech spans, off every word
    for a, b in sil_free:
        excluded.append({"start": round(a, 2), "end": round(b, 2), "sec": round(b - a, 2),
                         "reason": f"digital silence (under {SILENCE_DB:g} dBFS), not speech"})
    spans1 = subtract(spans, sil_free)

    nonword = []
    for a, b in _uncovered(spans1, cover):
        pieces = subtract([(a, b)], [(c, d) for c, d in sil_free])
        pieces = [(x, y) for x, y in pieces if y > x]
        if sum(y - x for x, y in pieces) < NONWORD_MIN_S:
            continue
        audio = np.concatenate([pcm[int(x * sr):int(y * sr)] for x, y in pieces])
        mod = syllable_modulation(audio, sr)
        if mod is not None and mod < MOD_MAX:
            for x, y in pieces:
                nonword.append((x, y))
                excluded.append({"start": round(x, 2), "end": round(y, 2), "sec": round(y - x, 2),
                                 "reason": f"no caption word, steady voiced sound (syllable rhythm {mod:.2f} < {MOD_MAX})"})
    spans2 = subtract(spans1, nonword)
    excluded.sort(key=lambda r: r["start"])
    info = {"vad_speech_sec": round(raw_sec, 2), "counted_speech_sec": round(sum(e - s for s, e in spans2), 2),
            "excluded_sec": round(sum(r["sec"] for r in excluded), 2), "excluded": excluded,
            "raw_window_coverage": round(raw_coverage(words, dur, pad), 3), "raw_window_min": MIN_RAW_COVERAGE}
    return spans2, excluded, info
