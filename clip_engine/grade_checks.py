"""Pass/fail rules for a finished clip, from measurements. Pure functions, no ffmpeg.

Each check returns {"name", "status", "detail"}; status is pass | fail | warn | n/a.
A check that cannot measure something says so (warn / n/a); it never passes by default.
"""
from . import hooks
from .config import (CRF, MIN_LEN, MAX_LEN, OUT_W, OUT_H, PUNCH_TARGET, PUNCH_HARD_CAP,
                     TARGET_LUFS, TRUE_PEAK, HOOK_MAX_SEC)

PASS, FAIL, WARN, NA = "pass", "fail", "warn", "n/a"
ALLOWED_AUDIO_FILTERS = ("afade", "loudnorm")
LUFS_TOL, LUFS_WARN = 1.0, 2.0
TP_FAIL = -1.0
MAX_PAN_PX_S = 700.0         # source px per second; faster reads as a whip pan


def _c(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def check_video(m):
    v, e = m.get("video") or {}, m.get("expected") or {}
    bad = []
    if (v.get("width"), v.get("height")) != (OUT_W, OUT_H):
        bad.append(f"size {v.get('width')}x{v.get('height')} (want {OUT_W}x{OUT_H})")
    if v.get("codec") != "h264":
        bad.append(f"codec {v.get('codec')}")
    if v.get("pix_fmt") != "yuv420p":
        bad.append(f"pix_fmt {v.get('pix_fmt')}")
    sf = e.get("source_fps")
    if sf and abs((v.get("fps") or 0) - sf) > 0.05:
        bad.append(f"fps {v.get('fps')} (source {sf})")
    ok = f"{v.get('width')}x{v.get('height')} {v.get('codec')} {v.get('pix_fmt')} {v.get('fps')} fps"
    return _c("video format", FAIL if bad else PASS, "; ".join(bad) or ok)


def check_duration(m):
    d = (m.get("video") or {}).get("duration") or 0
    ok = MIN_LEN <= d <= MAX_LEN
    return _c("length 25-45 s", PASS if ok else FAIL, f"{d:.2f} s")


def check_trim(m):
    v, a, e = m.get("video") or {}, m.get("audio") or {}, m.get("expected") or {}
    fps = v.get("fps") or e.get("source_fps") or 60
    bad = []
    if v.get("frames") != e.get("frames"):
        bad.append(f"frames {v.get('frames')} != expected {e.get('frames')}")
    if abs((v.get("duration") or 0) - (e.get("duration") or 0)) > 1.0 / fps + 0.002:
        bad.append(f"video {v.get('duration')} s vs expected {e.get('duration')} s")
    ad = a.get("duration")
    if ad is None:
        bad.append("no audio stream")
    elif abs(ad - (v.get("duration") or 0)) > 0.05:
        bad.append(f"audio {ad:.3f} s vs video {v.get('duration')} s")
    if m.get("short_frames"):
        bad.append(f"{m['short_frames']} frames repeated because the source ended")
    return _c("frame-accurate trim", FAIL if bad else PASS,
              "; ".join(bad) or f"{v.get('frames')} frames, video {v.get('duration')} s, audio {ad:.3f} s")


def check_crf(m):
    crf = m.get("crf")
    if crf is None:
        return _c("x264 CRF 17", WARN, "CRF not readable from the file")
    return _c("x264 CRF 17", PASS if abs(crf - CRF) < 0.01 else FAIL, f"crf={crf:g}")


def check_loudness(m):
    L = m.get("loud") or {}
    i, tp = L.get("i"), L.get("tp")
    if i is None or i == float("-inf"):
        return _c("loudness -14 LUFS", FAIL, "could not measure (silent audio?)")
    off = abs(i - TARGET_LUFS)
    st = PASS if off <= LUFS_TOL else WARN if off <= LUFS_WARN else FAIL
    detail = f"{i:.1f} LUFS (target {TARGET_LUFS:g}), LRA {L.get('lra')}, true peak {tp} dBFS"
    if tp is not None and tp > TP_FAIL:
        st, detail = FAIL, detail + f"; true peak above {TP_FAIL} dBFS"
    elif tp is not None and tp > TRUE_PEAK + 0.3 and st == PASS:
        st, detail = WARN, detail + f"; true peak above target {TRUE_PEAK}"
    return _c("loudness -14 LUFS", st, detail)


def check_audio_added(m):
    r = m.get("render") or {}
    extra = [f for f in r.get("audio_filters", []) if f not in ALLOWED_AUDIO_FILTERS]
    bad = []
    if r.get("audio_inputs") != 1:
        bad.append(f"{r.get('audio_inputs')} audio inputs (want 1: the stream)")
    if extra:
        bad.append("extra audio filters " + ",".join(extra))
    note = "no music/SFX added (one audio input, fades + loudnorm only); game/stream audio itself is as recorded"
    return _c("no added music/SFX", FAIL if bad else PASS, "; ".join(bad) or note)


def check_hook(m):
    a = m.get("ass") or {}
    t, text = a.get("hook_start"), a.get("hook_text") or ""
    if t is None or not text:
        return _c("hook within 1 s", FAIL, "no hook caption in the ASS")
    bad = []
    if t > HOOK_MAX_SEC:
        bad.append(f"hook starts at {t:.2f} s")
    if hooks.clean_hook(text) != text:
        bad.append("hook text is not clean (symbols, length)")
    return _c("hook within 1 s", FAIL if bad else PASS, "; ".join(bad) or f'"{text}" from {t:.2f} s')


def check_captions(m):
    a = m.get("ass") or {}
    bad = []
    if set(a.get("caption_styles", [])) != {"Main"}:
        bad.append(f"caption styles {a.get('caption_styles')} (want one: Main)")
    if a.get("max_highlights", 0) > 1:
        bad.append(f"{a['max_highlights']} highlighted words in one line")
    if not a.get("caption_lines"):
        bad.append("no caption lines")
    dur = (m.get("video") or {}).get("duration") or 0
    if a.get("last_end", 0) > dur + 0.05:
        bad.append("captions run past the clip end")
    return _c("one caption style, max 1 highlight", FAIL if bad else PASS,
              "; ".join(bad) or f"{a.get('caption_lines')} lines, max {a.get('max_highlights')} highlight per line")


def check_crop(m):
    r = m.get("render") or {}
    z = r.get("zoom_max")
    if z is None:
        return _c("tracked crop + punch-in", WARN, "no crop data")
    bad, warn = [], []
    if z > PUNCH_HARD_CAP + 1e-9:
        bad.append(f"punch-in {z * 100:.1f}% above hard cap {PUNCH_HARD_CAP * 100:.0f}%")
    elif z > PUNCH_TARGET + 1e-9:
        warn.append(f"punch-in {z * 100:.1f}% above {PUNCH_TARGET * 100:.0f}% target")
    if not r.get("crop_in_frame", False):
        bad.append("crop box leaves the frame")
    if r.get("pan_max_px_s", 0) > MAX_PAN_PX_S:
        warn.append(f"fast pan {r['pan_max_px_s']:.0f} px/s")
    cam = r.get("cam") or {}
    if cam.get("rect") and not cam.get("excluded", True):
        warn.append("facecam region still inside the crop")
    if (r.get("pan_total_px") or 0) < 20:
        warn.append("crop barely moved (tracking did little)")
    detail = (f"punch-in max {z * 100:.1f}%, pan max {r.get('pan_max_px_s', 0):.0f} px/s, "
              f"cam {cam.get('how', 'none')}")
    return _c("tracked crop + punch-in", FAIL if bad else WARN if warn else PASS,
              "; ".join(bad + warn) or detail)


def check_visual(m):
    v = m.get("visual")
    if not v:
        return _c("visual gate", NA, "not measured")
    return _c("visual gate", PASS if v["ok"] else FAIL, v["detail"])


def check_motion(m):
    v = m.get("motion")
    if not v:
        return _c("motion gate", NA, "not measured")
    return _c("motion gate", PASS if v["ok"] else FAIL, v["detail"])


ALL = (check_video, check_duration, check_trim, check_crf, check_loudness, check_audio_added,
       check_hook, check_captions, check_crop, check_visual, check_motion)


def run_all(m):
    return [f(m) for f in ALL]


def failed(checks):
    return [c for c in checks if c["status"] == FAIL]


def format_report(checks):
    icon = {PASS: "PASS", FAIL: "FAIL", WARN: "WARN", NA: "n/a "}
    lines = [f"{icon[c['status']]}  {c['name']}: {c['detail']}" for c in checks]
    nf, nw = len(failed(checks)), sum(c["status"] == WARN for c in checks)
    lines.append(f"GRADE: {'FAIL' if nf else 'PASS'} ({nf} failed, {nw} warnings, "
                 f"{sum(c['status'] == PASS for c in checks)} passed)")
    return "\n".join(lines)
