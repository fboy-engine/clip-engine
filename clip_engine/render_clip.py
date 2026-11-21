"""Render ONE clip: tracked 9:16 crop, slow punch-in, clean cut, burned captions + hook.

Python does the geometry (one sub-pixel affine crop per frame, so no filter-expression jitter)
and ffmpeg does decode and encode. Audio is the stream's own audio only: two short fades against
clicks and a two-pass loudnorm to -14 LUFS. No music, no SFX, no transitions are ever added.
"""
import gc
import json
import os
import re
import subprocess
import time
from fractions import Fraction

import numpy as np

from . import (asr_fix, balance, caption_lane, captions, cam_layout, config, download, hooks, layout, select_clean,
               subject, track, transcribe, trim, vision)
from .cropplan import punch_curve, smooth

OUT_W, OUT_H = config.OUT_W, config.OUT_H
GRAY_FPS, GRAY_W, GRAY_H = 4, 64, 36
FADE = 0.012
END_FADE = 0.15       # short fade out at the end (0.1-0.2 s is fine; no long fade to silence)
MOTION_FPS = 20
PICK_FPS = 10         # start picking samples the shown strip at the grade's rate
OPEN_TRIES = 3        # re-plan the cut at most this many times when the shown opening is frozen
NOMINAL_CAM = layout.NOMINAL_CAM
GLOSSARY = {"lucia": "Lucia"}


def frame_window(start, end, fps):
    """Snap the cut to the frame grid. Returns (start_s, n_frames, duration_s)."""
    n = max(1, round((end - start) * fps))
    return round(start * fps) / fps, n, n / fps


def fps_arg(fps):
    """Exact frame-rate string; NTSC rates (23.976, 29.97, 59.94) become n*1000/1001."""
    for base in (24, 30, 48, 50, 60):
        if abs(fps - base * 1000 / 1001) < 0.011:
            return f"{base * 1000}/1001"
    f = Fraction(fps).limit_denominator(1000)
    return f"{f.numerator}/{f.denominator}" if f.denominator != 1 else str(f.numerator)


# ---- tracking geometry ---------------------------------------------------

def gray_cmd(media, start, dur, fps=GRAY_FPS, w=GRAY_W, h=GRAY_H):
    return ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", media, "-t", f"{dur:.3f}",
            "-an", "-vf", f"fps={fps},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"]


def decode_gray(media, start, dur):
    p = subprocess.run(gray_cmd(media, start, dur), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("gray decode failed: " + p.stderr.decode(errors="replace")[-300:])
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(buf) // (GRAY_W * GRAY_H)
    return buf[:n * GRAY_W * GRAY_H].reshape(n, GRAY_H, GRAY_W)


def smooth_series(xs, sigma):
    xs = np.asarray(xs, dtype=np.float64)
    if sigma < 0.5 or len(xs) < 3:
        return xs
    r = int(3 * sigma)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    return np.convolve(np.pad(xs, r, mode="edge"), k / k.sum(), mode="valid")


def per_frame_x(points, n, fps, bounds, smooth_sec=0.5):
    """Crop left edge (source px, at zoom 0) for each output frame."""
    ts = np.arange(n) / fps
    x = np.interp(ts, [p[0] for p in points], [p[1] for p in points])
    return np.clip(smooth_series(x, smooth_sec * fps), bounds[0], bounds[1])


def boxes(xs, zs, src_w, src_h, lo=0.0, aspect=9 / 16):
    """Per-frame source box (left, top, w, h) at `aspect`, shrinking around its centre by the punch-in."""
    w0 = src_h * aspect
    out = []
    for x, z in zip(xs, zs):
        h = src_h / (1 + z)
        w = h * aspect
        left = min(max(x + w0 / 2 - w / 2, lo), src_w - w)
        out.append((float(left), float((src_h - h) / 2), float(w), float(h)))
    return out


def affine(box, out_w=OUT_W):
    left, top, w, _ = box
    s = out_w / w
    return np.array([[s, 0, -left * s], [0, s, -top * s]], dtype=np.float32)


def path_stats(bxs, fps, src_w, src_h, src_h0):
    lefts = np.array([b[0] + b[2] / 2 for b in bxs])           # centre x per frame
    v = np.abs(np.diff(lefts)) * fps if len(lefts) > 1 else np.array([0.0])
    zoom = [src_h0 / b[3] - 1 for b in bxs]
    inside = all(b[0] >= -1e-6 and b[1] >= -1e-6 and b[0] + b[2] <= src_w + 1e-6 and b[1] + b[3] <= src_h + 1e-6
                 for b in bxs)
    return {"zoom_max": round(float(max(zoom)), 4), "pan_max_px_s": round(float(v.max()), 1),
            "pan_total_px": round(float(lefts.max() - lefts.min()), 1), "crop_in_frame": bool(inside)}


# ---- audio ---------------------------------------------------------------

# gentle level matching so gunfire does not bury speech (2.2:1 above -21 dB, slow release: no pumping),
# and a ceiling limiter after loudnorm (0.84 linear = -1.5 dBFS)
COMP = "acompressor=threshold=-21dB:ratio=2.2:attack=20:release=350:knee=6dB"
LIMITER = "alimiter=limit=0.84:attack=5:release=60:level=disabled"
AUDIO_FILTERS = ["afade", "acompressor", "loudnorm", "alimiter"]


def audio_pre(dur, fade_out=FADE):
    return f"afade=t=in:st=0:d={FADE},afade=t=out:st={max(dur - fade_out, 0):.3f}:d={fade_out},{COMP}"


def loudnorm_measure_cmd(media, start, dur, fade_out=FADE):
    ln = f"loudnorm=I={config.TARGET_LUFS:g}:TP={config.TRUE_PEAK:g}:LRA={config.LRA:g}:print_format=json"
    return ["ffmpeg", "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", media,
            "-vn", "-af", f"{audio_pre(dur, fade_out)},{ln}", "-f", "null", "-"]


def parse_loudnorm(text):
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", text, re.S)
    if not m:
        raise ValueError("no loudnorm JSON in ffmpeg output")
    d = json.loads(m.group(0))
    return {k: float(d[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}


def loudnorm_filter(dur, m, fade_out=FADE):
    return (f"{audio_pre(dur, fade_out)},loudnorm=I={config.TARGET_LUFS:g}:TP={config.TRUE_PEAK:g}:LRA={config.LRA:g}"
            f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,{LIMITER}")


# ---- ffmpeg commands -----------------------------------------------------

def decode_cmd(media, start, n, fps):
    return ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", media, "-an",
            "-vf", f"fps={fps_arg(fps)}", "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]


def encode_cmd(media, start, dur, ass_path, out_path, fps, af, crf=config.CRF, preset=config.PRESET):
    # setparams writes primaries/transfer on the frames; the -color_* options alone left them "unknown" (ffmpeg 8)
    vf = (f"ass={ass_path}:fontsdir={captions.FONTS_DIR},scale=out_color_matrix=bt709:out_range=tv,"
          "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv,format=yuv420p")
    return ["ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}", "-r", fps_arg(fps), "-i", "-",
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", media,
            "-map", "0:v:0", "-map", "1:a:0", "-vf", vf, "-af", af,
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-t", f"{dur:.3f}", "-movflags", "+faststart", out_path]


def sharpen(img, amount=0.35, sigma=1.2):
    """Light unsharp for the upscaled cam block."""
    import cv2
    blur = cv2.GaussianBlur(img, (0, 0), sigma)
    return cv2.addWeighted(img, 1 + amount, blur, -amount, 0)


def render_frames(media, start, n, fps, bxs, src_w, src_h, enc_cmd, enc_log, cam_bxs=None):
    """cam_bxs None: one full-height 9:16 crop. Otherwise cam block on top, gameplay panel below."""
    import cv2
    size = src_w * src_h * 3
    panel_h = layout.PANEL_H if cam_bxs else OUT_H
    dec = subprocess.Popen(decode_cmd(media, start, n, fps), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    with open(enc_log, "w") as log:
        enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        last, short = None, 0
        try:
            for i in range(n):
                buf = dec.stdout.read(size)
                if len(buf) == size:
                    last = np.frombuffer(buf, np.uint8).reshape(src_h, src_w, 3)
                elif last is None:
                    raise RuntimeError("decoder produced no frames")
                else:
                    short += 1
                out = cv2.warpAffine(last, affine(bxs[i]), (OUT_W, panel_h), flags=cv2.INTER_LANCZOS4,
                                     borderMode=cv2.BORDER_REPLICATE)
                if cam_bxs:
                    cam = cv2.warpAffine(last, affine(cam_bxs[i]), (OUT_W, layout.CAM_H),
                                         flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
                    out = np.vstack([sharpen(cam), out])
                enc.stdin.write(out.tobytes())
        finally:
            enc.stdin.close()
            dec.stdout.close()
            dec.wait()
            rc = enc.wait()
    if rc != 0:
        raise RuntimeError("encoder failed: " + open(enc_log).read()[-500:])
    return short


# ---- words ---------------------------------------------------------------

def transcribe_words(media, start, end, model_name, cache_dir, pad=2.0, beam=5, language="en"):
    """large-v3 on the final window only (+pad for clean edges). Word times on the source-file clock."""
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    a0 = max(0.0, start - pad)
    audio = transcribe.decode_pcm(media, a0, end + pad)
    path = download_model(model_name, cache_dir=cache_dir)
    model = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 2)
    t = time.monotonic()
    segs, info = transcribe.transcribe_audio(model, audio, language, beam)
    sec = time.monotonic() - t
    words = asr_fix.flatten(segs)
    for w in words:
        w["s"] = round(w["s"] + a0, 3)
        w["e"] = round(w["e"] + a0, 3)
    meta = {"model": model_name, "beam": beam, "audio_sec": round(len(audio) / transcribe.SR, 1),
            "transcribe_sec": round(sec, 1), "n_words": len(words),
            "language_prob": round(getattr(info, "language_probability", 0.0) or 0.0, 3)}
    del model
    gc.collect()
    return words, meta


def decode_gray_at(media, start, dur, fps, w=96, h=54):
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", media, "-t", f"{dur:.3f}", "-an",
           "-vf", f"fps={fps},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("gray decode failed: " + p.stderr.decode(errors="replace")[-300:])
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(buf) // (w * h)
    return buf[:n * w * h].reshape(n, h, w)


def plan_cut(media, pr, start, end, words, cam_corner, cam_rect, payoff, notes, log):
    """Cut points: first real motion (with sound) at/after `start`; a payoff end at/before `end`.
    Returns (in_s, out_s, trim_info). Raises when the moment has no honest start or end."""
    src_w, src_h = pr["width"], pr["height"]
    env_end = min(end, pr["duration"] - 0.05)
    if env_end - start < config.MIN_LEN:
        raise ValueError(f"envelope {start:g}-{end:g}s is shorter than {config.MIN_LEN:g} s")
    # facecam on a short pilot sample so its constant motion does not hide a frozen game picture
    rect, mode = (tuple(cam_rect) if cam_rect else None), (cam_corner or "auto").strip().lower()
    if rect is None and mode != "none":
        corners = [cam_layout.norm_corner(mode)] if cam_layout.norm_corner(mode) else list(vision.CORNER_SEARCH)
        smp = vision.sample(media, start, min(10.0, env_end - start), src_w, src_h, corners)
        cam, _ = vision.choose_cam(smp["faces"], smp["n"], src_w, src_h)
        rect = cam["rect"] if cam else None
        del smp
    # search the WHOLE moment: gray frames at the grade's 10 fps, the strip the subject-tracked crop shows
    span = env_end - start
    gray = decode_gray_at(media, start, span, PICK_FPS, w=192, h=108)
    pw = layout.panel_wh(src_w, src_h)[0] if rect else src_h * 9 / 16
    bounds = cam_layout.crop_bounds(rect, src_w, pw)
    cx = smooth(track.centroid_x(gray, rect), alpha=0.4, deadband=0.03)
    lefts = [max(bounds[0], min(c * src_w - pw / 2, bounds[1])) / src_w for c in cx]
    strip = trim.crop_cols_path(gray, lefts, pw / src_w)
    pcm = transcribe.decode_pcm(media, start, env_end)
    rms = balance.rms_db(pcm, transcribe.SR, 0.05)
    rel = [{**w, "s": w["s"] - start, "e": w["e"] - start} for w in words]
    off, sinfo = trim.pick_start(strip, PICK_FPS, rms, 0.05, rel, 0.0, span, min_left=config.MIN_LEN)
    if off is None:
        raise ValueError(f"no start in {start:g}-{env_end:g}s where the shown strip moves and speech or a loud "
                         f"peak follows with {config.MIN_LEN:g} s left: skip this moment ({sinfo})")
    t0, snote = start + off, ""
    lo, hi = t0 + config.MIN_LEN, min(t0 + config.MAX_LEN, env_end)
    t1, why, enotes = trim.payoff_end(words, t0, lo, hi, rms, 0.05, payoff, rms_t0=start)
    notes += enotes
    info = {"asked": [start, end], "in": round(t0, 3), "out": round(t1, 3), "start_motion": sinfo,
            "start_note": snote, "end_reason": why, "facecam_masked": bool(rect)}
    log(f"trim: in {t0:.2f}s (asked {start:g}, +{off:.2f}s; {sinfo.get('reason')}), out {t1:.2f}s: {why}")
    return t0, t1, info


def shown_opening(media, s_f, fps, box, src_w):
    """Check the cut start against the strip the viewer will actually see (the 9:16 crop), with the same
    opening gate the grade runs. Returns (ok, first_good_offset_s or None, info). Motion outside the
    crop (an undetected facecam, the far side of the map) must not count as "first motion"."""
    dur = trim.START_SEARCH + 3.5
    gray = decode_gray_at(media, s_f, dur, MOTION_FPS, w=192, h=108)
    strip = trim.crop_cols(gray, box[0] / src_w, box[2] / src_w)
    pcm = transcribe.decode_pcm(media, s_f, s_f + dur)
    rms = balance.rms_db(pcm, transcribe.SR, 0.05)
    t, info = trim.visible_start(strip, MOTION_FPS, rms, 0.05)
    info["strip"] = [round(box[0] / src_w, 3), round(box[2] / src_w, 3)]
    return (t is not None and t < 0.06), t, info


def seeded_cam(media, s_f, dur, cam_rect, src_w, src_h, log):
    """The cam region a person pointed at. The relaxed wide-scale detector looks for the face inside it; with
    3+ hits the cam block follows the face, otherwise it shows the region itself (static). Either way it is
    reported as user-seeded, never as face-verified."""
    rect = tuple(float(v) for v in cam_rect)
    obs, n = vision.faces_in_rect(media, s_f, dur, rect, src_w, src_h)
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    corner = ("top" if cy < 0.5 else "bottom") + "-" + ("left" if cx < 0.5 else "right")
    hit = round(100 * len(obs) / n, 1) if n else 0.0
    if len(obs) >= 3:
        how = "user-seeded, face-tracked"
    else:
        wpx = (rect[2] - rect[0]) * src_w
        obs, how = [(0.0, cx * src_w, cy * src_h, wpx / layout.FACE_BOX_FACTOR)], "user-seeded, static region (no face found)"
    log(f"cam seeded from rect {rect}: {how}, relaxed face hits {hit}% of {n} samples")
    return {"corner": corner, "obs": obs, "hit_pct": hit, "rect": rect, "how": how}


def write_cam_diag(media, start, end, src_w, src_h, out_dir, cam_rect, log):
    """out/cam_diag.json and out/source_contact.jpg (+ native source_frame_N.jpg): written before any gate can fail."""
    try:
        diag = vision.cam_diag(media, start, max(end - start, 1.0), src_w, src_h,
                               os.path.join(out_dir, "source_contact.jpg"), seed_rect=cam_rect)
    except Exception as e:
        diag = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
    with open(os.path.join(out_dir, "cam_diag.json"), "w") as f:
        json.dump(diag, f, indent=1)
    log("cam diag: " + "; ".join(diag_lines(diag)))
    return diag


def diag_lines(dg):
    """One short line per corner (strict/relaxed/wide face hits, % of frames) for logs and the run summary."""
    if not dg or dg.get("error"):
        return ["cam diagnostics failed: " + str((dg or {}).get("error"))]
    rows = [f"{c}: strict {v['strict']}/{dg['frames']} ({v.get('strict_pct')}%), relaxed {v['relaxed']}, "
            f"wide {v.get('wide')} ({v.get('wide_pct')}%), max face {v['max_face_px']} px"
            for c, v in dg.get("corners", {}).items()]
    if dg.get("seed"):
        sd = dg["seed"]
        rows.append(f"seed rect {sd['rect']}: strict {sd['strict']}, wide {sd['wide']} ({sd.get('wide_pct')}%), "
                    f"max face {sd['max_face_px']} px")
    return rows


# ---- the render ----------------------------------------------------------

def render(media, start, end, out_dir, hook_text="", cam_corner=None, cam_rect=None, words=None,
           model="large-v3", cache_dir=None, preset=config.PRESET, log=print, payoff=""):
    """Returns the render.json dict (also written to out_dir). Raises on anything it cannot do honestly."""
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.monotonic()
    pr = download.probe_media(media)
    src_w, src_h, fps = pr["width"], pr["height"], pr["fps"]
    notes, timings = [], {}
    # cam diagnostics FIRST, on the whole moment, written even when the render fails later
    diag = write_cam_diag(media, start, min(end, pr["duration"] - 0.05), src_w, src_h, out_dir, cam_rect, log)
    meta = None
    if words is None:
        words, meta = transcribe_words(media, start, end, model, cache_dir)
        timings["asr_sec"] = meta["transcribe_sec"]
    words = asr_fix.fix_words(words, GLOSSARY)
    shift, open_notes = 0.0, []
    for attempt in range(OPEN_TRIES):
        notes.clear()                       # each attempt re-plans, so its notes start fresh
        s_c, e_c, trim_info = plan_cut(media, pr, start + shift, end, words, cam_corner, cam_rect, payoff, notes, log)
        s_f, n, dur = frame_window(s_c, e_c, fps)
        if not (config.MIN_LEN <= dur <= config.MAX_LEN):
            raise ValueError(f"clip would be {dur:.1f} s, outside {config.MIN_LEN:g}-{config.MAX_LEN:g} s")
        if s_f + dur > pr["duration"] + 0.01:
            raise ValueError("window runs past the end of the source file")
        log(f"window: asked {start:g}-{end:g}s, cut {s_f:.3f}s +{dur:.3f}s ({n} frames @ {fps:g} fps)")

        # captions + hook (word times shifted onto the clip clock)
        cw = [w for w in asr_fix.to_clip_time(words, s_f) if w["s"] < dur]
        for w in cw:
            w["e"] = min(w["e"], dur)
        cw = asr_fix.mask_profanity(cw)
        lane = caption_lane.lanes(False)
        hook, hook_src, hook_match = hooks.choose_hook(asr_fix.mask_text(hook_text), cw)
        cap_words = captions.drop_hook_words(cw, hook, hooks.HOOK_SHOW)      # the hook shows the opening words
        ass = captions.to_ass(cap_words, 0.0, OUT_W, OUT_H, lane["caption_margin_v"], lane["seam_y"])
        ass = hooks.add_hook(ass, hook, lane["hook_margin_v"])
        ass_path = os.path.join(out_dir, "clip.ass")
        with open(ass_path, "w") as f:
            f.write(ass)
        log(f"hook: {hook!r} ({hook_src}, matches first 3 s: {hook_match}); {len(cw)} caption words")

        # one sampling pass: faces for the cam block, small frames for the subject tracker
        t = time.monotonic()
        mode = (cam_corner or "auto").strip().lower()
        if mode == "none" or cam_rect:        # a given rect is trusted as the cam region (seed): no corner search
            corners = []
        elif cam_layout.norm_corner(mode):
            corners = [cam_layout.norm_corner(mode)]
        else:
            corners = list(vision.CORNER_SEARCH)
        smp = vision.sample(media, s_f, dur, src_w, src_h, corners)
        cam, cam_search = vision.choose_cam(smp["faces"], smp["n"], src_w, src_h) if corners else (None, {})
        if cam_rect and mode != "none":
            cam = seeded_cam(media, s_f, dur, cam_rect, src_w, src_h, log)
            cam_search = {"seeded_by_person": True, "face_hit_pct": cam["hit_pct"]}
        use_cam = cam is not None
        if corners and not use_cam:
            notes.append("no facecam verified by face detection: gameplay-only layout")
        rect = (tuple(cam_rect) if cam_rect else cam["rect"]) if use_cam else None
        aspect = layout.PANEL_ASPECT if use_cam else 9 / 16
        pw = layout.panel_wh(src_w, src_h)[0] if use_cam else src_h * 9 / 16
        bounds = cam_layout.crop_bounds(rect, src_w, pw)

        # subject track -> smoothed crop path for the gameplay panel
        dets = [vision.detect_people(f, rect) for f in smp["small"]]
        motion = track.centroid_x(vision.small_gray(smp["small"]), rect)
        sub = subject.build(dets, motion)
        cxs = smooth(sub["cx"], alpha=0.4, deadband=0.03)
        pts = [(round(tt, 3), round(max(bounds[0], min(c * src_w - pw / 2, bounds[1])), 1))
               for tt, c in zip(smp["times"], cxs)] or [(0.0, round((bounds[0] + bounds[1]) / 2, 1))]
        xs = per_frame_x(pts, n, fps, bounds)
        zs = punch_curve(n, min(config.PUNCH_TARGET, 0.05) if use_cam else config.PUNCH_TARGET)
        bxs = boxes(xs, zs, src_w, src_h, bounds[0], aspect)
        stats = path_stats(bxs, fps, src_w, src_h, src_h)
        idx = [min(int(round(tt * fps)), n - 1) for tt in smp["times"]]
        sub_plan = subject.inside_stats(sub["box"], smp["times"], [bxs[j][0] for j in idx],
                                        [bxs[j][2] for j in idx], src_w)
        stats["subject"] = sub_plan

        cbx = None
        cam_info = {"found": use_cam, "mode": mode, "search": cam_search}
        if use_cam:
            path = layout.face_path(cam["obs"], n, fps)
            cbx = layout.cam_boxes(path, layout.region_px(rect, src_w, src_h))
            cam_info.update({"corner": cam["corner"], "face_hit_pct": cam["hit_pct"], "face_w_px": round(path[2], 1),
                             "rect": list(rect), "box_px": [round(cbx[0][2]), round(cbx[0][3])],
                             "upscale": round(layout.upscale(cbx), 2)})
        stats["layout"] = "cam_top" if use_cam else "full"
        stats["cam_block"] = cam_info
        stats["panel"] = {"y0": layout.CAM_H if use_cam else 0, "h": layout.PANEL_H if use_cam else OUT_H}
        stats["cam"] = {"rect": list(rect) if rect else None, "corner": cam["corner"] if use_cam else None,
                        "how": cam.get("how", "face-verified") if use_cam else "none",
                        "excluded": not any(cam_layout.visible_in_crop(rect, b[0], b[2], src_w) for b in bxs)}
        timings["track_sec"] = round(time.monotonic() - t, 1)
        del smp
        log(f"layout {stats['layout']}: cam {cam_info.get('corner')} face hits {cam_info.get('face_hit_pct')}%, "
            f"subject tracked {sub_plan['tracked_pct']}% inside {sub_plan['inside_pct']}%, "
            f"zoom max {stats['zoom_max'] * 100:.1f}%, pan max {stats['pan_max_px_s']:.0f}px/s")

        # the opening must be real motion in the strip that is SHOWN (same gate as the grade, never loosened)
        ok_open, t_ok, oinfo = shown_opening(media, s_f, fps, bxs[0], src_w)
        trim_info["asked"] = [start, end]
        trim_info["opening_check"] = {"ok": bool(ok_open), "tries": attempt + 1, "shift_s": round(shift, 3), **oinfo}
        if ok_open:
            break
        if t_ok is None or attempt == OPEN_TRIES - 1:
            raise ValueError(f"opening is frozen or silent in the shown strip for {trim.START_SEARCH:g} s after "
                             f"{s_f:.2f}s: skip this moment ({oinfo})")
        open_notes.append(f"opening froze in the shown strip: start moved +{t_ok:.2f}s to {s_f + t_ok:.2f}s")
        log(f"opening: shown strip is not moving at {s_f:.2f}s, retrying from {s_f + t_ok:.2f}s")
        shift = s_f + t_ok - start

    notes[:0] = open_notes

    # audio: measure, then normalise to -14 LUFS (stream audio only)
    t = time.monotonic()
    p = subprocess.run(loudnorm_measure_cmd(media, s_f, dur, END_FADE), capture_output=True, text=True)
    ln = parse_loudnorm(p.stderr)
    af = loudnorm_filter(dur, ln, END_FADE)
    log(f"audio: input {ln['input_i']:.1f} LUFS, tp {ln['input_tp']:.1f} dBFS -> target {config.TARGET_LUFS:g}")
    timings["loudnorm_sec"] = round(time.monotonic() - t, 1)

    t = time.monotonic()
    out_path = os.path.join(out_dir, "clip.mp4")
    short = render_frames(media, s_f, n, fps, bxs, src_w, src_h,
                          encode_cmd(media, s_f, dur, ass_path, out_path, fps, af, preset=preset),
                          os.path.join(out_dir, "encode.log"), cbx)
    timings["render_sec"] = round(time.monotonic() - t, 1)

    r = {"source": {"width": src_w, "height": src_h, "fps": fps, "duration": pr["duration"]},
         "asked": {"start": start, "end": end}, "cut": {"start": s_f, "end": round(s_f + dur, 3), "duration": round(dur, 4), "frames": n},
         "expected": {"frames": n, "duration": round(dur, 4), "source_fps": fps}, "short_frames": short,
         "hook": {"text": hook, "source": hook_src, "match": hook_match}, "trim": trim_info, "words": len(cw), "asr": meta, "notes": notes,
         "word_spans": [[round(w["s"], 3), round(w["e"], 3)] for w in cw],
         "cam_diag": diag, "crop_points": pts, "loudnorm_input": ln, "timings": timings,
         "encode": {"codec": "libx264", "crf": config.CRF, "preset": preset},
         "grade_inputs": {"cam_diag": diag, "audio_inputs": 1, "audio_filters": list(AUDIO_FILTERS), "trim": trim_info,
                          "hook": {"text": hook, "source": hook_src, "match": hook_match}, **stats}}
    with open(os.path.join(out_dir, "render.json"), "w") as f:
        json.dump(r, f, indent=1)
    timings["total_sec"] = round(time.monotonic() - t0, 1)
    log(f"rendered {out_path} in {timings['render_sec']}s (total {timings['total_sec']}s)")
    return r
