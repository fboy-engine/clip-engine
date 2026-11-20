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


def payoff_gate(w):
    """Needs speech/reaction AND a payoff (energy peak above the average).

    Pure action with no voice fails. Flat rambling fails.
    """
    dur = w.end - w.start
    if not (MIN_LEN <= dur <= MAX_LEN):
        return False, "length"
    if w.speech_ratio < 0.35:
        return False, "not enough speech"
    lift = w.peak_rms_db - w.mean_rms_db
    if not (REACTION.search(w.text) or lift >= 6):
        return False, "no reaction/peak"
    if lift < 3:
        return False, "no payoff (flat energy)"
    return True, "ok"


def score(w):
    dur = w.end - w.start
    s = 2.0 * w.speech_ratio
    s += min(max(w.peak_rms_db - w.mean_rms_db, 0), 12) / 12 * 3.0
    s += 1.5 if REACTION.search(w.text) else 0
    s -= abs(dur - 35) / 35          # prefer ~35 s
    return s


def rank(windows, top=5):
    kept = [(score(w), w) for w in windows if payoff_gate(w)[0]]
    kept.sort(key=lambda t: t[0], reverse=True)
    return [w for _, w in kept[:top]]
