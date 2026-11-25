"""Speech-dense windows: where the streamer talks (almost) without a break for 26-30 s.

The caption backstop (words must cover 75% of the whole clip) cannot be met by a clip with long pauses, so the source
window must be chosen where speech is continuous. Pure python on the per-second VAD speech share (analyze.speech_per_sec);
nothing here changes a gate. render_start/render_end hints pad the window so the --dense planner can anchor on a word.
"""

LENGTHS = (26, 28, 30)


def longest_quiet(sp, a, b, thr=0.5):
    best = run = 0
    for v in sp[a:b]:
        run = run + 1 if v < thr else 0
        best = max(best, run)
    return best


def dense_windows(sp, offset=0.0, lengths=LENGTHS, top=8, min_pct=0.80, max_quiet=2, pad=3):
    """[{file_start, file_end, speech_pct, longest_quiet_s, stream_start, render_start, render_end}] best first, no overlap."""
    n = len(sp)
    if n < min(lengths):
        return []
    pre = [0.0]
    for v in sp:
        pre.append(pre[-1] + v)
    cands = []
    for L in lengths:
        for s in range(0, n - L + 1):
            pct = (pre[s + L] - pre[s]) / L
            if pct >= min_pct and longest_quiet(sp, s, s + L) <= max_quiet:
                cands.append((round(pct, 3), L, s))
    cands.sort(key=lambda c: (-c[0], -c[1], c[2]))
    out, used = [], []
    for pct, L, s in cands:
        if any(s < b and s + L > a for a, b in used):
            continue
        used.append((s, s + L))
        out.append({"file_start": s, "file_end": s + L, "speech_pct": round(pct * 100, 1),
                    "longest_quiet_s": longest_quiet(sp, s, s + L), "stream_start": round(offset + s, 1),
                    "render_start": max(0, s - pad), "render_end": min(n, s + L + pad)})
        if len(out) >= top:
            break
    return out
