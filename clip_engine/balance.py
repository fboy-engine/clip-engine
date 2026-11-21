"""Voice vs game-audio balance of the finished clip. Pure numpy."""
import numpy as np


def rms_db(pcm, sr, win=0.05):
    n = int(sr * win)
    k = len(pcm) // n
    if k == 0:
        return np.array([])
    x = np.asarray(pcm[:k * n], dtype=np.float64).reshape(k, n)
    return 20 * np.log10(np.sqrt((x ** 2).mean(axis=1)) + 1e-9)


def balance(pcm, sr, spans, win=0.05, floor=-60.0):
    """voice_db: median 50 ms RMS inside word spans; loud_db: 98th percentile 50 ms RMS over the
    whole clip (gunfire, engines); gap_db = loud - voice. A big gap means speech sits under the game."""
    r = rms_db(pcm, sr, win)
    if r.size == 0:
        return {"voice_db": None, "loud_db": None, "gap_db": None}
    t = (np.arange(len(r)) + 0.5) * win
    sp = np.zeros(len(r), dtype=bool)
    for s, e in spans:
        sp |= (t >= s) & (t <= e)
    voice = r[sp & (r > floor)]
    audible = r[r > floor]
    loud = round(float(np.percentile(audible, 98)), 1) if audible.size else None
    if voice.size < 3 or loud is None:
        return {"voice_db": None, "loud_db": loud, "gap_db": None}
    v = float(np.median(voice))
    return {"voice_db": round(v, 1), "loud_db": loud, "gap_db": round(loud - v, 1)}
