"""Sample/render window check: an empty or zero window must never reach the benchmark or the render silently."""
import json
import math
import os

MIN_WINDOW_SEC = 4.0


def check_window(start, end, duration=None, name_start="render_start", name_end="render_end"):
    """Return (start, end) as floats or raise ValueError naming the missing/bad input.

    Rejects: not a number, negative start, end <= start (this covers 0/0 and equal values), a window shorter than
    MIN_WINDOW_SEC, and a start beyond the end of the media. An end past the media is clamped (not silent: the
    caller logs it).
    """
    try:
        s, e = float(start), float(end)
    except (TypeError, ValueError):
        raise ValueError(f"{name_start}/{name_end} must be numbers (seconds in the window file), got {start!r}/{end!r}")
    if not (math.isfinite(s) and math.isfinite(e)):
        raise ValueError(f"{name_start}/{name_end} must be finite numbers, got {start!r}/{end!r}")
    if s < 0:
        raise ValueError(f"{name_start} is negative ({s:g}): set {name_start} to a time in the window file")
    if e <= s:
        both0 = s == 0 and e == 0
        raise ValueError(
            f"empty sample window: {name_start}={s:g} and {name_end}={e:g}"
            + (" (both are 0, the workflow defaults)" if both0 else "")
            + f". Set {name_start} and {name_end} to the clip's seconds in the window file, e.g. 45 and 85.")
    if e - s < MIN_WINDOW_SEC:
        raise ValueError(f"sample window too short: {e - s:g} s between {name_start}={s:g} and {name_end}={e:g} "
                         f"(need at least {MIN_WINDOW_SEC:g} s)")
    if duration is not None:
        if s >= duration - 0.5:
            raise ValueError(f"{name_start}={s:g} is at or past the end of the media ({duration:.1f} s)")
        e = min(e, duration - 0.05)
        if e - s < MIN_WINDOW_SEC:
            raise ValueError(f"sample window runs past the media: only {e - s:g} s are left after {name_start}={s:g} "
                             f"(media is {duration:.1f} s)")
    return s, e


def write_error_report(out_dir, error, inputs=None, name="cam_bench"):
    """Always leave out/<name>.json (and .md) with the failure so the artifact upload has something to ship."""
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, name + ".json"), "w") as f:
            json.dump({"error": str(error), "inputs": inputs or {}}, f, indent=1)
        with open(os.path.join(out_dir, name + ".md"), "w") as f:
            f.write(f"# {name} FAILED\n\n```\n{error}\n```\n\nInputs: `{json.dumps(inputs or {})}`\n")
    except OSError:
        pass
