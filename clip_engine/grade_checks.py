"""Pass/fail rules for a finished clip, from measurements. Pure functions, no ffmpeg.

Each check returns {"name", "status", "detail"}; status is pass | fail | warn | n/a.
A check that cannot measure something says so (warn / n/a); it never passes by default.
"""
from . import cam_sr, captions, hooks
from .config import (CRF, MIN_LEN, MAX_LEN, OUT_W, OUT_H, PUNCH_TARGET, PUNCH_HARD_CAP,
                     TARGET_LUFS, TRUE_PEAK, HOOK_MAX_SEC)

PASS, FAIL, WARN, NA = "pass", "fail", "warn", "n/a"
ALLOWED_AUDIO_FILTERS = ("afade", "acompressor", "loudnorm", "alimiter")
LUFS_TOL, LUFS_WARN = 1.0, 2.0
TP_FAIL = TRUE_PEAK          # the finished file must stay at or below the true-peak target (-1.5 dBFS)
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
    for key, want in (("color_range", "tv"), ("color_space", "bt709"), ("color_transfer", "bt709"),
                      ("color_primaries", "bt709")):
        if v.get(key) != want:
            bad.append(f"{key} {v.get(key)} (want {want})")
    ok = (f"{v.get('width')}x{v.get('height')} {v.get('codec')} {v.get('pix_fmt')} {v.get('fps')} fps, "
          f"{v.get('bit_rate_kbps')} kbps, bt709 tv")
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
    if len(text.split()) > hooks.MAX_WORDS:
        bad.append(f"hook has {len(text.split())} words (max {hooks.MAX_WORDS})")
    elif not hooks.is_complete(text):
        bad.append("hook is not a complete line (too short or ends mid-thought)")
    first = a.get("first_caption") or ""
    if first and hooks.duplicates(text, first):
        bad.append(f'hook repeats the first caption line ("{first}")')
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
    fonts = a.get("fonts")
    if fonts is not None and (captions.FONT not in fonts or any("dejavu" in f.lower() for f in fonts)):
        bad.append(f"fonts {fonts}: want the bundled {captions.FONT}, not DejaVu")
    if a.get("unmasked_profanity"):
        bad.append("unmasked profanity: " + ", ".join(a["unmasked_profanity"][:5]))
    return _c("one caption style, max 1 highlight", FAIL if bad else PASS,
              "; ".join(bad) or f"{a.get('caption_lines')} lines, max {a.get('max_highlights')} highlight per line, "
              f"font {', '.join(fonts or [])}")


def check_caption_lane(m):
    """Captions sit on the seam when the cam block is on top, and no text is drawn over other text."""
    name = "caption lane: on the seam, no text over text"
    a = m.get("ass") or {}
    bands = a.get("bands")
    if not bands:
        return _c(name, WARN, "no text bands to check")
    bad = []
    ov = captions.band_overlaps(bands)
    if ov:
        bad.append("text over text: " + ", ".join(f"{x}/{y} at {t:g}s" for x, y, t in ov[:4]))
    if (m.get("render") or {}).get("layout") == "cam_top":
        main = [b for b in bands if b[0] == "Main"]
        off = [b for b in main if abs((b[3] + b[4]) / 2 - captions.SEAM_Y) > 12]
        if not main or off:
            bad.append(f"captions are not centred on the seam (y {captions.SEAM_Y})")
    return _c(name, FAIL if bad else PASS, "; ".join(bad) or f"{len(bands)} text bands, none overlapping")


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


def check_layout(m):
    r = m.get("render") or {}
    lay = r.get("layout")
    cb = r.get("cam_block") or {}
    name = "layout: cam on top, bigger gameplay below"
    if lay == "cam_top":
        from .layout import CAM_H, PANEL_H
        return _c(name, PASS if PANEL_H > CAM_H else FAIL,
                  f"cam {cb.get('corner')} ({(r.get('cam') or {}).get('how')}; face in {cb.get('face_hit_pct')}% of samples), cam block {CAM_H}px "
                  f"({cb.get('box_px')} px source, x{cb.get('upscale')} upscale), panel {PANEL_H}px")
    if lay == "full":
        return _c(name, WARN, "no facecam found: gameplay-only 9:16 layout (reported, not hidden)")
    return _c(name, NA, "not measured")


def check_cam_quality(m):
    """The cam block must beat plain lanczos on the finished clip: face fidelity >= 0.96 against the untouched real crop,
    blockiness lower (see cam_sr.blockiness_ok), edge strength higher, no ghosting in the in-between frames."""
    name = "cam block quality: enhanced beats plain lanczos"
    r = m.get("render") or {}
    if r.get("layout") != "cam_top":
        return _c(name, NA, "no cam block in this layout")
    cq = r.get("cam_quality")
    if not cq or not cq.get("enhanced") or not cq.get("lanczos"):
        return _c(name, FAIL, "no cam quality measurement: the enhanced cam block was not verified")
    ok, reasons = cam_sr.quality_verdict(cq)
    e, o = cq["enhanced"], cq["lanczos"]
    nums = (f"fidelity {e['fidelity']:.4f} (min {cam_sr.FIDELITY_MIN:g}), blockiness {e['blockiness']:.4f} vs plain lanczos "
            f"{o['blockiness']:.4f}, edge {e['edge']:.2f} vs {o['edge']:.2f}; {cq.get('method')} {cq.get('blend')} share "
            f"at {cq.get('cam_fps')} fps, {cq.get('interp')}, {cq.get('sr_seconds')} s")
    return _c(name, PASS if ok else FAIL, nums + (" | " + "; ".join(reasons) if reasons else ""))


def check_subject(m):
    """Subject (the player character) inside the gameplay panel in >= 95% of sampled frames."""
    name = "subject inside panel >= 95%"
    o = m.get("subject_out") or {}
    plan = (m.get("render") or {}).get("subject") or {}
    if o.get("error") or not o:
        return _c(name, WARN, "output re-detection unavailable: " + str(o.get("error", "not run")))
    det, inside = o.get("detected_pct", 0.0), o.get("inside_pct")
    unseen = o.get("unseen_times", [])
    if inside is None or det < 25:
        return _c(name, WARN, f"cannot verify: person detected in only {det}% of {o.get('samples')} samples "
                  f"(plan-level: tracked {plan.get('tracked_pct')}%, inside {plan.get('inside_pct')}%)")
    detail = (f"{inside}% inside on {det}% detected frames of {o.get('samples')}; plan-level tracked "
              f"{plan.get('tracked_pct')}% inside {plan.get('inside_pct')}%")
    if inside < 95.0:
        return _c(name, FAIL, detail + "; cut at " + ", ".join(f"{t:g}s" for t in o.get("cut_times", [])[:20]))
    if det < 60:
        return _c(name, WARN, detail + f"; unseen at {', '.join(f'{t:g}s' for t in unseen[:12])}")
    return _c(name, PASS, detail)


def check_balance(m):
    b = m.get("balance") or {}
    if b.get("error") or b.get("gap_db") is None:
        return _c("voice vs game audio", WARN, "not measurable: " + str(b.get("error", "too little speech")))
    detail = f"voice {b['voice_db']} dB, loud peaks {b['loud_db']} dB (50 ms RMS), gap {b['gap_db']} dB"
    return _c("voice vs game audio", WARN if b["gap_db"] > 14 else PASS, detail)


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


def check_opening(m):
    o = m.get("opening")
    name = "opening: no freeze, real motion (first 3 s)"
    if not o or o.get("error"):
        return _c(name, WARN, "not measured: " + str((o or {}).get("error", "not run")))
    return _c(name, PASS if o["ok"] else FAIL, o["detail"])


def check_cold_open(m):
    c = m.get("cold_open") or {}
    name = "cold open: sound from the first frames"
    on = c.get("onset_s")
    if on is None:
        return _c(name, WARN, "not measurable (too little audio)")
    detail = f"sound at {on:.2f} s (first 0.3 s {c.get('first_db')} dB vs median {c.get('median_db')} dB)"
    return _c(name, PASS if on <= 0.3 else WARN if on <= 0.6 else FAIL, detail)


def check_ending(m):
    name = "ending: lands on a payoff, no mid-word cut"
    spans = m.get("word_spans") or []
    dur = (m.get("video") or {}).get("duration") or 0
    why = ((m.get("render") or {}).get("trim") or {}).get("end_reason", "")
    if not spans or not dur:
        return _c(name, WARN, "no word times to check the ending against")
    if any(s < dur - 0.03 < e for s, e in spans):
        return _c(name, FAIL, "the clip ends in the middle of a word")
    tail = dur - spans[-1][1]
    detail = f"{tail:.2f} s after the last word; {why}"
    return _c(name, WARN if tail > 1.6 else PASS, detail + ("; long dead tail" if tail > 1.6 else ""))


def check_hook_match(m):
    h = (m.get("render") or {}).get("hook") or {}
    name = "hook matches the first seconds"
    if h.get("match") is True:
        return _c(name, PASS, f"{h.get('source')}: \"{h.get('text')}\"")
    if h.get("source") == "given_unverified":
        return _c(name, WARN, f"\"{h.get('text')}\" is not spoken in the first 3 s (fine only if it is shown on screen)")
    return _c(name, WARN, "no hook that matches the first 3 s")


ALL = (check_video, check_duration, check_trim, check_crf, check_loudness, check_balance, check_audio_added,
       check_hook, check_hook_match, check_opening, check_cold_open, check_ending, check_captions, check_caption_lane,
       check_layout, check_cam_quality,
       check_subject, check_crop, check_visual, check_motion)


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


def _tail(m):
    spans = m.get("word_spans") or []
    dur = (m.get("video") or {}).get("duration") or 0
    why = ((m.get("render") or {}).get("trim") or {}).get("end_reason", "")
    if not spans or not dur:
        return "no word times"
    return f"{dur - spans[-1][1]:.2f} s after the last word; {why}"


def key_numbers(m):
    """The short block the run summary leads with."""
    v, L, b = m.get("video") or {}, m.get("loud") or {}, m.get("balance") or {}
    r, o = m.get("render") or {}, m.get("subject_out") or {}
    cb = r.get("cam_block") or {}
    rows = [f"resolution {v.get('width')}x{v.get('height')}, {v.get('fps')} fps, {v.get('bit_rate_kbps')} kbps, "
            f"{m.get('size_mb')} MB, duration {v.get('duration')} s",
            f"loudness {L.get('i')} LUFS, LRA {L.get('lra')}, true peak {L.get('tp')} dBFS",
            f"voice {b.get('voice_db')} dB vs loud peaks {b.get('loud_db')} dB (gap {b.get('gap_db')})",
            f"layout {r.get('layout')}; cam found: {'yes' if cb.get('found') else 'NO'}"
            + (f" ({cb.get('corner')}, face in {cb.get('face_hit_pct')}%, x{cb.get('upscale')} upscale)" if cb.get("found") else ""),
            f"cut in {(m.get('cut') or {}).get('start')} s -> out {(m.get('cut') or {}).get('end')} s of the window file; "
            f"end: {((r.get('trim') or {}).get('end_reason'))}",
            f"opening freeze check: {(m.get('opening') or {}).get('detail', 'not measured')} "
            f"({'ok' if (m.get('opening') or {}).get('ok') else 'FAIL/unmeasured'})",
            f"subject in frame: {o.get('inside_pct')}% of detected ({o.get('detected_pct')}% of {o.get('samples')} samples); "
            f"render-time plan {((r.get('subject') or {}).get('inside_pct'))}%",
            f"hook: \"{(r.get('hook') or {}).get('text')}\" ({(r.get('hook') or {}).get('source')}, "
            f"matches first 3 s: {(r.get('hook') or {}).get('match')})",
            f"ending: {_tail(m)}",
            ]
    dg = r.get("cam_diag") or {}
    if dg.get("corners"):
        rows.append("cam diagnostics (frames with a face, strict/relaxed of %s): " % dg.get("frames")
                    + "; ".join(f"{c} {v['strict']}/{v['relaxed']} (max face {v['max_face_px']} px)"
                                for c, v in dg["corners"].items())
                    + ((f"; seed rect {dg['seed']['rect']}: strict {dg['seed']['strict']} ({dg['seed'].get('strict_pct')}%)")
                       if dg.get("seed") else ""))
    elif dg.get("error"):
        rows.append("cam diagnostics failed: " + str(dg["error"]))
    oc = (r.get("trim") or {}).get("opening_check")
    if oc:
        rows.append(f"opening start check on the shown strip: ok={oc.get('ok')}, tries {oc.get('tries')}, start shifted "
                    f"+{oc.get('shift_s')} s, {oc.get('detail')}")
    return "\n".join(rows)
