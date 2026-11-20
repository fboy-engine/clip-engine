"""Visual and motion gates for the rendered clip. numpy only; frames are small gray stacks."""
import numpy as np


def visual_gate(frames, bad_frac=0.1, min_sharp=2.0):
    """(ok, detail). Rejects mostly black/blown-out/flat footage (menus, loading, fades)."""
    f = np.asarray(frames, dtype=np.float32)
    if len(f) == 0:
        return False, "no frames"
    mean = f.mean(axis=(1, 2))
    std = f.std(axis=(1, 2))
    sharp = (np.abs(np.diff(f, axis=2)).mean(axis=(1, 2)) + np.abs(np.diff(f, axis=1)).mean(axis=(1, 2))) / 2
    bad = ((mean < 20) | (mean > 235) | (std < 10)).mean()
    ok = bad <= bad_frac and float(np.median(sharp)) >= min_sharp
    return bool(ok), f"bad frames {bad * 100:.0f}%, median sharpness {np.median(sharp):.1f}, mean luma {mean.mean():.0f}"


def motion_gate(series, floor=0.003, min_active=0.6, max_static_run=5):
    """(ok, detail) on a per-second motion series (analyze.motion_series): enough of the clip
    moves and there is no long frozen stretch."""
    m = np.asarray(series, dtype=np.float64)
    if m.size == 0:
        return False, "no motion data"
    active = m >= floor
    run = best = 0
    for a in active:
        run = 0 if a else run + 1
        best = max(best, run)
    ok = active.mean() >= min_active and best <= max_static_run
    return bool(ok), f"{active.mean() * 100:.0f}% of seconds moving, longest static run {best}s"
