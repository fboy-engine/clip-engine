"""Candidate finding from signals, not from speech alone.

GTA streams have long quiet action stretches (chases, crashes, shootouts with a
silent streamer), so a moment can come from any of: a voice-activity burst, an
audio-energy peak, a screen-motion burst, or a quiet gap that sets one up.
Pure numpy on per-second series; the ffmpeg decode helpers are thin wrappers.
"""
import subprocess

import numpy as np

from .config import MIN_LEN, MAX_LEN
from .rank_local import Window

SR = 16000
TARGET_LEN = 35.0


def rms_db(audio, sr=SR, hop=1.0):
    """Loudness per second in dBFS."""
    n = int(sr * hop)
    k = len(audio) // n
    if k == 0:
        return []
    x = np.asarray(audio[:k * n], dtype=np.float64).reshape(k, n)
    rms = np.sqrt((x ** 2).mean(axis=1))
    return (20 * np.log10(np.maximum(rms, 1e-6))).tolist()


def speech_per_sec(spans, n):
    """Fraction (0..1) of each second covered by VAD speech spans."""
    out = [0.0] * n
    for s, e in spans:
        for i in range(max(int(s), 0), min(int(np.ceil(e)), n)):
            out[i] += max(0.0, min(e, i + 1) - max(s, i))
    return [min(v, 1.0) for v in out]


def zscores(xs):
    a = np.asarray(xs, dtype=np.float64)
    if a.size == 0:
        return []
    sd = a.std()
    return ((a - np.median(a)) / (sd if sd > 1e-9 else 1.0)).tolist()


def moving_average(xs, k=3):
    a = np.asarray(xs, dtype=np.float64)
    if a.size == 0 or k <= 1:
        return a.tolist()
    return np.convolve(a, np.ones(k) / k, mode="same").tolist()


# ---- screen motion -------------------------------------------------------

def motion_cmd(media, fps=4, w=64, h=36):
    return ["ffmpeg", "-v", "error", "-nostdin", "-i", media, "-an",
            "-vf", f"fps={fps},scale={w}:{h}:flags=area,format=gray",
            "-f", "rawvideo", "-"]


def motion_series(frames, fps=4):
    """frames: (n, h, w) uint8 gray. Per-second mean absolute frame difference, 0..1."""
    f = np.asarray(frames, dtype=np.float32)
    if len(f) < 2:
        return []
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2)) / 255.0
    d = np.concatenate([[0.0], d])
    k = len(d) // fps
    return d[:k * fps].reshape(k, fps).mean(axis=1).tolist() if k else []


def decode_motion(media, fps=4, w=64, h=36):
    p = subprocess.run(motion_cmd(media, fps, w, h), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg motion decode failed: " + p.stderr.decode(errors="replace")[-300:])
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(buf) // (w * h)
    return motion_series(buf[:n * w * h].reshape(n, h, w), fps)


# ---- cut points ----------------------------------------------------------

def quiet_runs(db, min_len=1, margin=6.0):
    """(start, end) second runs clearly quieter than the window median."""
    if not db:
        return []
    thr = float(np.median(db)) - margin
    runs, s = [], None
    for i, v in enumerate(db):
        if v < thr and s is None:
            s = i
        elif v >= thr and s is not None:
            if i - s >= min_len:
                runs.append((s, i))
            s = None
    if s is not None and len(db) - s >= min_len:
        runs.append((s, len(db)))
    return runs


def cut_points(spans, db, min_gap=0.3):
    """Natural places to cut: middles of gaps between speech spans and of quiet runs."""
    pts = []
    spans = sorted(spans)
    for (_, e1), (s2, _) in zip(spans, spans[1:]):
        if s2 - e1 >= min_gap:
            pts.append((e1 + s2) / 2)
    pts += [(s + e) / 2 for s, e in quiet_runs(db)]
    return sorted(pts)


def snap(t, points, radius=3.0):
    near = [p for p in points if abs(p - t) <= radius]
    return min(near, key=lambda p: abs(p - t)) if near else t


# ---- events and windows --------------------------------------------------

def heat_series(db, speech, motion):
    """Per-second 'something happens': loud (vs median), moving (vs median), voiced."""
    n = len(db)
    zdb = zscores(db)
    zm = zscores(motion[:n]) if motion else []
    zm = zm + [0.0] * (n - len(zm))
    sp = list(speech[:n]) + [0.0] * (n - len(speech))
    return moving_average([max(zdb[i], 0) + max(zm[i], 0) + 0.5 * sp[i] for i in range(n)], 3)


def find_peaks(heat, min_sep=10, thr=None):
    a = np.asarray(heat, dtype=np.float64)
    if a.size == 0 or a.std() < 1e-9:
        return []
    thr = a.mean() + a.std() if thr is None else thr
    chosen = []
    for i in sorted(range(len(a)), key=lambda j: -a[j]):
        if a[i] < thr:
            break
        if all(abs(i - j) >= min_sep for j in chosen):
            chosen.append(i)
    return sorted(chosen)


def _overlap(a, b):
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    return inter / max(min(a.end - a.start, b.end - b.start), 1e-6)


def dedupe(windows, max_overlap=0.4):
    kept = []
    for w in sorted(windows, key=lambda x: -x.heat):
        if all(_overlap(w, k) < max_overlap for k in kept):
            kept.append(w)
    return kept


def build_windows(db, speech, motion, spans, lead=22.0, tail=13.0, min_sep=10):
    """Candidate windows (25-45 s) around heat peaks, cut points snapped to quiet/gaps.
    Times are seconds from the start of the analysed media."""
    n = len(db)
    if n < MIN_LEN:
        return []
    heat = heat_series(db, speech, motion)
    pts = cut_points(spans, db)
    msd = float(np.std(motion)) if motion else 0.0
    out = []
    for p in find_peaks(heat, min_sep):
        e = min(float(n), max(0.0, p - lead) + lead + tail)
        s = max(0.0, e - (lead + tail))
        s2, e2 = snap(s, pts), snap(e, pts)
        if MIN_LEN <= e2 - s2 <= MAX_LEN and 0 <= s2 and e2 <= n:
            s, e = s2, e2
        if e - s < MIN_LEN:
            continue
        i0, i1 = int(s), max(int(np.ceil(e)), int(s) + 1)
        seg_db, seg_sp = db[i0:i1], speech[i0:i1]
        seg_m = motion[i0:i1] if motion else []
        lift = (max(seg_m) - float(np.mean(seg_m))) / msd if seg_m and msd > 1e-9 else 0.0
        out.append(Window(start=round(s, 2), end=round(e, 2), text="",
                          speech_ratio=float(np.mean(seg_sp)) if seg_sp else 0.0,
                          peak_rms_db=float(max(seg_db)), mean_rms_db=float(np.mean(seg_db)),
                          motion_lift=round(lift, 2), heat=round(float(heat[p]), 3),
                          peak_t=float(p)))
    return dedupe(out)


def reasons(w):
    r = []
    lift = w.peak_rms_db - w.mean_rms_db
    if lift >= 6:
        r.append(f"audio peak +{lift:.0f} dB over the window average")
    if w.motion_lift >= 2.5:
        r.append(f"screen-motion burst ({w.motion_lift:.1f} sd)")
    if w.speech_ratio >= 0.35:
        r.append(f"{w.speech_ratio * 100:.0f}% speech")
    elif w.speech_ratio < 0.15:
        r.append("mostly no voice (action stretch)")
    return r or ["weak signals"]
