"""Local candidate ranking with a payoff gate. No network, deterministic."""
import re
from dataclasses import dataclass

from .config import MIN_LEN, MAX_LEN

REACTION = re.compile(r"\b(no way|oh my god|omg|what the|holy|insane|crazy|bro|dude|"
                      r"let'?s go|wait|are you serious|unbelievable|finally)\b", re.I)


@dataclass
class Window:
    start: float
    end: float
    text: str
    speech_ratio: float      # 0..1 of window with voice (VAD)
    peak_rms_db: float       # loudest second, dBFS
    mean_rms_db: float
    motion_lift: float = 0.0  # screen-motion burst above the window mean, in sd (0 = unknown)
    heat: float = 0.0         # combined signal strength at the peak (analyze.heat_series)
    peak_t: float = 0.0       # second of the peak, relative to the analysed media


ACTION_MOTION = 2.5   # screen-motion burst (sd above window mean) for a voiceless moment
ACTION_AUDIO = 4.0    # and an audio lift (dB) that goes with it (crash, gunfire, engine)


def payoff_gate(w):
    """A moment needs a payoff, from either path:

    voice path:  speech + (reaction words or a loud peak) + energy lift.
    action path: a screen-motion burst AND an audio lift, speech not required, because
                 GTA has quiet action stretches. Flat rambling and idle footage fail.
    """
    dur = w.end - w.start
    if not (MIN_LEN <= dur <= MAX_LEN):
        return False, "length"
    lift = w.peak_rms_db - w.mean_rms_db
    if w.speech_ratio >= 0.35 and lift >= 3 and (REACTION.search(w.text) or lift >= 6):
        return True, "ok"
    if w.motion_lift >= ACTION_MOTION and lift >= ACTION_AUDIO:
        return True, "ok (action)"
    if w.speech_ratio < 0.35 and w.motion_lift < ACTION_MOTION:
        return False, "not enough speech or action"
    if lift < 3:
        return False, "no payoff (flat energy)"
    return False, "no reaction/peak"


def score(w):
    dur = w.end - w.start
    s = 2.0 * w.speech_ratio
    s += min(max(w.peak_rms_db - w.mean_rms_db, 0), 12) / 12 * 3.0
    s += 1.5 if REACTION.search(w.text) else 0
    s += min(max(w.motion_lift, 0), 4) / 4 * 2.0
    s -= abs(dur - 35) / 35          # prefer ~35 s
    return s


def rank(windows, top=5):
    kept = [(score(w), w) for w in windows if payoff_gate(w)[0]]
    kept.sort(key=lambda t: t[0], reverse=True)
    return [w for _, w in kept[:top]]
