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

from . import asr_fix, caption_lane, captions, cam_layout, config, download, hooks, select_clean, track, transcribe
from .cropplan import punch_curve

OUT_W, OUT_H = config.OUT_W, config.OUT_H
GRAY_FPS, GRAY_W, GRAY_H = 4, 64, 36
FADE = 0.012
NOMINAL_CAM = {"bottom-left": (0.0, 0.62, 0.26, 1.0), "bottom-right": (0.74, 0.62, 1.0, 1.0),
               "top-left": (0.0, 0.0, 0.26, 0.38), "top-right": (0.74, 0.0, 1.0, 0.38)}
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


def boxes(xs, zs, src_w, src_h, lo=0.0):
    """Per-frame source box (left, top, w, h): 9:16, shrinks around its centre by the punch-in."""
    w0 = src_h * 9 / 16
    out = []
    for x, z in zip(xs, zs):
        h = src_h / (1 + z)
        w = h * 9 / 16
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

def audio_pre(dur):
    return f"afade=t=in:st=0:d={FADE},afade=t=out:st={max(dur - FADE, 0):.3f}:d={FADE}"


def loudnorm_measure_cmd(media, start, dur):
    ln = f"loudnorm=I={config.TARGET_LUFS:g}:TP={config.TRUE_PEAK:g}:LRA={config.LRA:g}:print_format=json"
    return ["ffmpeg", "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", media,
            "-vn", "-af", f"{audio_pre(dur)},{ln}", "-f", "null", "-"]


def parse_loudnorm(text):
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", text, re.S)
    if not m:
        raise ValueError("no loudnorm JSON in ffmpeg output")
    d = json.loads(m.group(0))
    return {k: float(d[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}


def loudnorm_filter(dur, m):
    return (f"{audio_pre(dur)},loudnorm=I={config.TARGET_LUFS:g}:TP={config.TRUE_PEAK:g}:LRA={config.LRA:g}"
            f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")


# ---- ffmpeg commands -----------------------------------------------------

def decode_cmd(media, start, n, fps):
    return ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", media, "-an",
            "-vf", f"fps={fps_arg(fps)}", "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]


def encode_cmd(media, start, dur, ass_path, out_path, fps, af, crf=config.CRF, preset=config.PRESET):
    vf = f"ass={ass_path},scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
    return ["ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}", "-r", fps_arg(fps), "-i", "-",
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", media,
            "-map", "0:v:0", "-map", "1:a:0", "-vf", vf, "-af", af,
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "tv",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-t", f"{dur:.3f}", "-movflags", "+faststart", out_path]


def render_frames(media, start, n, fps, bxs, src_w, src_h, enc_cmd, enc_log):
    import cv2
    size = src_w * src_h * 3
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
                out = cv2.warpAffine(last, affine(bxs[i]), (OUT_W, OUT_H), flags=cv2.INTER_LANCZOS4,
                                     borderMode=cv2.BORDER_REPLICATE)
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


# ---- the render ----------------------------------------------------------

def render(media, start, end, out_dir, hook_text="", cam_corner=None, cam_rect=None, words=None,
           model="large-v3", cache_dir=None, preset=config.PRESET, log=print):
    """Returns the render.json dict (also written to out_dir). Raises on anything it cannot do honestly."""
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.monotonic()
    pr = download.probe_media(media)
    src_w, src_h, fps = pr["width"], pr["height"], pr["fps"]
    notes, timings = [], {}
    meta = None
    if words is None:
        words, meta = transcribe_words(media, start, end, model, cache_dir)
        timings["asr_sec"] = meta["transcribe_sec"]
    words = asr_fix.fix_words(words, GLOSSARY)
    s_c, e_c, cn = select_clean.select_clean(words, start, end)
    notes += cn
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
    lane = caption_lane.lanes(False)
    ass = captions.to_ass(cw, 0.0, OUT_W, OUT_H, lane["caption_margin_v"])
    hook, hook_src = hooks.pick_hook(hook_text, cw)
    ass = hooks.add_hook(ass, hook, lane["hook_margin_v"])
    ass_path = os.path.join(out_dir, "clip.ass")
    with open(ass_path, "w") as f:
        f.write(ass)
    log(f"hook: {hook!r} ({hook_src}); {len(cw)} caption words")

    # facecam + tracked crop
    t = time.monotonic()
    gray = decode_gray(media, s_f, dur)
    corner = cam_layout.norm_corner(cam_corner)
    rect, how = None, "none"
    if cam_rect:
        rect, how = tuple(cam_rect), "given"
    elif corner:
        rect = cam_layout.static_rect(gray, corner, max_std=0.02)
        how = "detected static block"
        if not rect:
            rect, how = NOMINAL_CAM[corner], "assumed nominal rect (no static block found)"
            notes.append(f"facecam {corner} not measured; nominal rectangle assumed")
    w0 = src_h * 9 / 16
    bounds = cam_layout.crop_bounds(rect, src_w, w0)
    plan = track.plan(gray, GRAY_FPS, src_w, src_h, mask_rect=rect, bounds=bounds)
    xs = per_frame_x(plan["points"], n, fps, bounds)
    zs = punch_curve(n, config.PUNCH_TARGET)
    bxs = boxes(xs, zs, src_w, src_h, bounds[0])
    stats = path_stats(bxs, fps, src_w, src_h, src_h)
    stats["cam"] = {"rect": list(rect) if rect else None, "how": how, "corner": corner,
                    "excluded": not any(cam_layout.visible_in_crop(rect, b[0], b[2], src_w) for b in bxs)}
    timings["track_sec"] = round(time.monotonic() - t, 1)
    log(f"crop: zoom max {stats['zoom_max'] * 100:.1f}%, pan max {stats['pan_max_px_s']:.0f}px/s, cam {how}")

    # audio: measure, then normalise to -14 LUFS (stream audio only)
    t = time.monotonic()
    p = subprocess.run(loudnorm_measure_cmd(media, s_f, dur), capture_output=True, text=True)
    ln = parse_loudnorm(p.stderr)
    af = loudnorm_filter(dur, ln)
    log(f"audio: input {ln['input_i']:.1f} LUFS, tp {ln['input_tp']:.1f} dBFS -> target {config.TARGET_LUFS:g}")
    timings["loudnorm_sec"] = round(time.monotonic() - t, 1)

    t = time.monotonic()
    out_path = os.path.join(out_dir, "clip.mp4")
    short = render_frames(media, s_f, n, fps, bxs, src_w, src_h,
                          encode_cmd(media, s_f, dur, ass_path, out_path, fps, af, preset=preset),
                          os.path.join(out_dir, "encode.log"))
    timings["render_sec"] = round(time.monotonic() - t, 1)

    r = {"source": {"width": src_w, "height": src_h, "fps": fps, "duration": pr["duration"]},
         "asked": {"start": start, "end": end}, "cut": {"start": s_f, "duration": round(dur, 4), "frames": n},
         "expected": {"frames": n, "duration": round(dur, 4), "source_fps": fps}, "short_frames": short,
         "hook": {"text": hook, "source": hook_src}, "words": len(cw), "asr": meta, "notes": notes,
         "crop_points": plan["points"], "loudnorm_input": ln, "timings": timings,
         "encode": {"codec": "libx264", "crf": config.CRF, "preset": preset},
         "grade_inputs": {"audio_inputs": 1, "audio_filters": ["afade", "loudnorm"], **stats}}
    with open(os.path.join(out_dir, "render.json"), "w") as f:
        json.dump(r, f, indent=1)
    timings["total_sec"] = round(time.monotonic() - t0, 1)
    log(f"rendered {out_path} in {timings['render_sec']}s (total {timings['total_sec']}s)")
    return r
