"""Render ONE clip: tracked 9:16 crop, slow punch-in, clean cut, burned captions + hook.

Python does the geometry (one sub-pixel affine crop per frame, so no filter-expression jitter)
and ffmpeg does decode and encode. Audio is the stream's own audio only: two short fades against
clicks and a two-pass loudnorm to -14 LUFS. No music, no SFX, no transitions are ever added.
"""
import gc
import hashlib
import json
import os
import re
import subprocess
import time
from fractions import Fraction

import numpy as np

from . import (asr_fix, balance, caption_lane, caption_sources, captions, captions_pass, cam_enhance, cam_layout, camfind, camresolve, cam_sr, config,
               download, exclusion, hooks, layout, select_clean, srcinfo, subject, track, transcribe, trim, vision)
from .cropplan import punch_curve, smooth

OUT_W, OUT_H = config.OUT_W, config.OUT_H
GRAY_FPS, GRAY_W, GRAY_H = 4, 64, 36
FADE = 0.012
END_FADE = 0.15       # short fade out at the end (0.1-0.2 s is fine; no long fade to silence)
MOTION_FPS = 20
PICK_FPS = 10         # start picking samples the shown strip at the grade's rate
TRACK_GATE_PCT = 85.0  # sampled frames with the subject fully inside the gameplay crop; below it the render refuses the clip
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


def tracking_masks(rect, zones, src_w, src_h):
    """Fraction rects the subject tracker ignores: the cam and every overlay zone (cam, chat, side panel, counters)."""
    m = [tuple(float(v) for v in rect)] if rect else []
    for z in (zones or {}).values():
        m.append((z[0] / src_w, z[1] / src_h, z[2] / src_w, z[3] / src_h))
    return m


def tracking_report(sub, sub_plan, times, fps, src_w=None, lefts=None, widths=None, bounds=None, cxs=None, margin=0.01):
    """out/tracking.json content: the gate number, where each sample's subject came from, the static detections dropped."""
    src = sub.get("source") or []
    n = len(src) or 1
    return {"gate_pct": TRACK_GATE_PCT, "in_panel_pct": sub_plan["in_panel_pct"], "tracked_pct": sub_plan["tracked_pct"],
            "inside_pct_of_tracked": sub_plan["inside_pct"], "samples": sub_plan["samples"], "sample_fps": fps,
            "sources_pct": {k: round(100.0 * src.count(k) / n, 1) for k in ("person", "vehicle", "motion", "none")},
            "static_dropped": [c for c in sub.get("static", []) if c.get("static")],
            "clusters_seen": sub.get("static", []), "cut_times": sub_plan["cut_times"],
            "per_sample": [{"t": round(float(t), 2), "source": s, "cx": round(float(c), 3)}
                           for t, s, c in zip(times, src, sub["cx"])],
            **_crop_diag(sub, times, src_w, lefts, widths, bounds, cxs, margin)}


def _crop_diag(sub, times, src_w, lefts, widths, bounds, cxs, margin):
    """Diagnostics only (no gate reads this): per sample the tracked box and the crop in source px and why the box is not inside.
    why: ok | no_subject | box_wider_than_crop | clamped_right | clamped_left | offset (crop not centred on the box: lag/smoothing)."""
    if not src_w or lefts is None:
        return {}
    rows, counts = [], {}
    for i, (t, left, cw) in enumerate(zip(times, lefts, widths)):
        b = sub["box"][i]
        r = {"t": round(float(t), 2), "crop_l": round(float(left)), "crop_r": round(float(left + cw)),
             "cx_smoothed": round(float(cxs[i]), 3) if cxs is not None else None}
        if b is None:
            why = "no_subject"
        else:
            x0, x1 = b[0] * src_w, b[2] * src_w
            m = margin * cw
            r.update({"box_x0": round(float(x0)), "box_x1": round(float(x1)), "box_w": round(float(x1 - x0)),
                      "label": (b[5] if len(b) > 5 else None), "off_px": round(float((x0 + x1) / 2 - (left + cw / 2)))})
            if x0 >= left + m and x1 <= left + cw - m:
                why = "ok"
            elif x1 - x0 > cw - 2 * m:
                why = "box_wider_than_crop"
            elif x1 > left + cw - m and bounds and left >= bounds[1] - 1:
                why = "clamped_right"
            elif x0 < left + m and bounds and left <= bounds[0] + 1:
                why = "clamped_left"
            else:
                why = "offset"
        r["why"] = why
        unf = (sub.get("unframeable") or [[]] * len(times))[i]
        if unf:
            r["unframeable"] = [{"x0": round(float(u[0] * src_w)), "x1": round(float(u[2] * src_w)),
                                 "label": (u[5] if len(u) > 5 else None)} for u in unf[:3]]
        counts[why] = counts.get(why, 0) + 1
        rows.append(r)
    return {"crop_bounds_px": [round(float(v)) for v in bounds] if bounds else None, "cut_reasons": counts, "crop_samples": rows}


def panel_bounds(rect, zones, src_w, pw):
    """(min, max) crop left edge. With known overlay zones the crop stays between them (and the render
    refuses when it cannot); otherwise the old cam-corner rule."""
    if zones:
        b = exclusion.crop_left_bounds(zones, src_w, pw)
        if b is None:
            raise ValueError(f"a {pw:.0f} px wide gameplay crop does not fit between the stream overlays: skip")
        return b
    return cam_layout.crop_bounds(rect, src_w, pw)


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
# and a ceiling limiter after loudnorm (config.LIMITER_CEIL_DB; the AAC encode overshoots a few tenths of a dB)
COMP = "acompressor=threshold=-21dB:ratio=2.2:attack=20:release=350:knee=6dB"


def limiter(ceil_db):
    return f"alimiter=limit={10 ** (ceil_db / 20):.3f}:attack=5:release=60:level=disabled"


LIMITER = limiter(config.LIMITER_CEIL_DB)
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


def loudnorm_filter(dur, m, fade_out=FADE, ceil_db=None):
    lim = LIMITER if ceil_db is None else limiter(ceil_db)
    return (f"{audio_pre(dur, fade_out)},loudnorm=I={config.TARGET_LUFS:g}:TP={config.TRUE_PEAK:g}:LRA={config.LRA:g}"
            f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,{lim}")


def remux_audio_cmd(media, start, dur, video_in, af, out_path):
    """Keep the encoded video as is (-c:v copy), redo only the audio from the source with filter chain af."""
    return ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", video_in,
            "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", media,
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af", af,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-t", f"{dur:.3f}", "-movflags", "+faststart", out_path]


def final_true_peak(media, start, dur, out_path, ln, log=print, measure=None, run=subprocess.run):
    """Final check on the FINISHED clip: ebur128 true peak must be <= config.TRUE_PEAK. If not, redo the audio
    only with the limiter ceiling 1 dB lower (up to config.TP_PASSES times). Returns a report dict."""
    if measure is None:
        from . import grade_clip
        measure = grade_clip.loudness
    ceil, passes = config.LIMITER_CEIL_DB, []
    for k in range(config.TP_PASSES + 1):
        L = measure(out_path)
        tp = L.get("tp")
        passes.append({"ceil_db": ceil, "tp": tp, "i": L.get("i")})
        log(f"true peak check: {tp} dBFS (limit {config.TRUE_PEAK:g}), limiter ceiling {ceil:g} dBFS")
        if tp is not None and tp <= config.TRUE_PEAK:
            return {"ok": True, "tp": tp, "limit": config.TRUE_PEAK, "ceil_db": ceil, "passes": passes}
        if k == config.TP_PASSES:
            break
        ceil -= 1.0
        tmp = out_path + ".tp.mp4"
        p = run(remux_audio_cmd(media, start, dur, out_path, loudnorm_filter(dur, ln, END_FADE, ceil), tmp),
                capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError("audio re-encode for the true-peak check failed: " + (p.stderr or "")[-400:])
        os.replace(tmp, out_path)
    return {"ok": False, "tp": passes[-1]["tp"], "limit": config.TRUE_PEAK, "ceil_db": ceil, "passes": passes}


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


def denoise(img):
    """Light edge-preserving denoise of the small source cam crop, before the lanczos upscale."""
    import cv2
    return cv2.bilateralFilter(img, 5, 20, 5)


def default_b3(warning):
    """The cam chain used when the benchmark was skipped: Real-ESRGAN at the start SR share, no deblock. Only the
    benchmark gate is skipped; fidelity, blockiness, edge and ghosting are still measured on the real render."""
    return {"method": "realesrgan", "cam_fps": cam_sr.B3_FPS_TIERS[0], "deblock": "none", "deblock_vf": "",
            "denoise_vf": cam_sr.DENOISE_VF, "blend": cam_sr.BLEND_START, "ok": True, "reasons": [], "bench_skipped": True,
            "reason": "benchmark skipped: " + str(warning)[:300]}


def load_b3(out_dir):
    """The cam chain the benchmark step chose (out/cam_bench.json, key b3). Raises a clear ValueError when there is
    none or when it says the quality gate failed or only plain lanczos is left: that is never shipped."""
    p = os.path.join(out_dir, "cam_bench.json")
    if not os.path.isfile(p):
        raise ValueError("cam benchmark step must run before the render (no cam_bench.json in the output folder)")
    with open(p) as f:
        d = json.load(f)
    if d.get("error"):
        raise ValueError(f"cam benchmark failed, so the cam block cannot be enhanced: {d['error']}")
    if d.get("skipped"):                # too few real crops: no bench gate, default chain; the render's own gates still decide
        return default_b3(d.get("warning", "benchmark skipped"))
    b3 = d.get("b3")
    if not b3:
        raise ValueError("cam_bench.json has no b3 section (benchmark from an older build)")
    if b3.get("method") == "lanczos":
        why = "; ".join(f"{k}: {v}" for k, v in (b3.get("rejected") or {}).items()) or b3.get("reason", "")
        raise ValueError(f"plain lanczos is not shipped: no neural upscaler is usable ({why})")
    if not b3.get("ok") or b3.get("blend") is None:
        raise ValueError("cam quality gate failed in the benchmark: " + "; ".join(b3.get("reasons") or ["no SR share passes"]))
    return b3


def clip_file_info(path, probe=None):
    """sha256, resolution, fps and bitrate of the finished clip.mp4 (the run summary shows them)."""
    info = (probe or srcinfo.media_info)(path)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"sha256": h.hexdigest(), "width": info.get("width"), "height": info.get("height"), "fps": info.get("fps"),
            "video_kbps": info.get("video_kbps"), "size_mb": info.get("size_mb"), "duration": info.get("duration"),
            "vcodec": info.get("vcodec")}


def summary_md(r):
    """Run-summary text: source format and bitrate, the clip file (sha256, resolution, bitrate) and the cam block numbers.
    Empty string when render.json has none of it."""
    if not r or not (r.get("source_format") or r.get("clip_file") or r.get("cam_quality") or r.get("rendered_gate")):
        return ""
    L = ["### Source and clip"]
    sf = r.get("source_format")
    if sf:
        L.append(f"- source file: {srcinfo.info_line(sf)}")
    cf = r.get("clip_file")
    if cf:
        br = f"{cf['video_kbps']} kbps" if cf.get("video_kbps") else "bitrate unknown"
        L += [f"- clip.mp4: {cf.get('width')}x{cf.get('height')} {cf.get('fps')} fps, {br}, {cf.get('size_mb')} MB",
              f"- clip.mp4 sha256: `{cf.get('sha256')}`"]
    cf2, rg = r.get("cam_find"), r.get("rendered_gate")
    if cf2:
        L += ["", "### Cam find and rendered gate",
              f"- cam: {'generic detector' if cf2.get('generic') else 'given / known layout'}, rect {cf2.get('rect_px')}, "
              f"region used {cf2.get('region_px')}, confidence {cf2.get('confidence')}, zones {sorted((cf2.get('zones') or {}).keys())}"]
    if rg:
        L.append(f"- rendered gate: **{'PASS' if rg.get('ok') else 'FAIL'}** on {len(rg.get('frames') or [])} frames"
                 + ("" if rg.get("ok") else " - " + "; ".join(rg.get("reasons") or [])))
    cq = r.get("cam_quality")
    if cq:
        e, o = cq.get("enhanced") or {}, cq.get("lanczos") or {}
        ok, reasons = cam_sr.quality_verdict(cq) if e and o else (False, ["no measurement"])
        L += ["", "### Cam block (1080x540)",
              f"- chain: {cq.get('chain') or cq.get('method')}",
              f"- {cq.get('method')} share {cq.get('blend')} at {cq.get('cam_fps')} fps SR, {cq.get('interp')} in-betweens, "
              f"deblock {cq.get('deblock') or 'none'}, SR time {cq.get('sr_seconds')} s",
              f"- measured on the rendered frames: fidelity {e.get('fidelity')} (min {cam_sr.FIDELITY_MIN:g}), blockiness "
              f"{e.get('blockiness')} (plain lanczos {o.get('blockiness')}), edge {e.get('edge')} (plain lanczos {o.get('edge')})",
              f"- ghost check: {'ok' if (cq.get('ghost') or {}).get('ok', True) else 'FAILED'}; gate: **{'PASS' if ok else 'FAIL'}**"
              + ("" if ok else " - " + "; ".join(reasons))]
    return "\n".join(L) + "\n"


def render_frames(media, start, n, fps, bxs, src_w, src_h, enc_cmd, enc_log, cam_bxs=None, cam_region=None,
                  cam_stream=None, probe=None):
    """cam_bxs None: one full-height 9:16 crop. Otherwise cam block on top, gameplay panel below. With a cam_stream
    (B3) the block comes from the enhancement chain; without one the old bilateral + lanczos4 + unsharp path runs."""
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
                if cam_bxs and cam_stream is not None:
                    rx, ry, rw, rh = cam_stream.roi
                    real_roi = last[ry:ry + rh, rx:rx + rw]
                    blk = cam_stream.block(i, real_roi)
                    if probe is not None and i in probe["idx"]:
                        cam_enhance.probe_frame(probe, i, blk, real_roi, cam_bxs[min(i, len(cam_bxs) - 1)], (rx, ry))
                    out = np.vstack([blk, out])
                elif cam_bxs:
                    cb, csrc = cam_bxs[i], last
                    if cam_region:      # denoise the small crop, then lanczos up to 1080x540, then a light sharpen
                        rx0, ry0, rx1, ry1 = cam_region
                        csrc = denoise(last[ry0:min(ry1, src_h), rx0:min(rx1, src_w)])
                        cb = (cb[0] - rx0, cb[1] - ry0, cb[2], cb[3])
                    cam = cv2.warpAffine(csrc, affine(cb), (OUT_W, layout.CAM_H),
                                         flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
                    out = np.vstack([sharpen(cam), out])
                enc.stdin.write(out.tobytes())
        finally:
            enc.stdin.close()
            dec.stdout.close()
            dec.wait()
            if cam_stream is not None:
                cam_stream.close()
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


def plan_cut(media, pr, start, end, words, cam_corner, cam_rect, payoff, notes, log, zones=None):
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
    bounds = panel_bounds(rect, zones, src_w, pw)
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
    """Verify the seeded cam region: a LARGE, STABLE face (strict detector) on the sampled frames. Returns the
    cam dict, or None when no cam is verified (the caller refuses the clip: no full-frame fallback)."""
    rect = tuple(float(v) for v in cam_rect)
    obs, n = vision.faces_in_rect(media, s_f, dur, rect, src_w, src_h)
    rect_wh = ((rect[2] - rect[0]) * src_w, (rect[3] - rect[1]) * src_h)
    ok, info, large = vision.verify_cam(obs, n, rect_wh)
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    corner = ("top" if cy < 0.5 else "bottom") + "-" + ("left" if cx < 0.5 else "right")
    log(f"cam seed {rect}: {'VERIFIED' if ok else 'NOT verified'}: {info['large_faces']} large faces "
        f"(>= {info['min_face_px']} px) in {n} samples = {info['hit_pct']}% (needs {info['needs_pct']}%), "
        f"spread {info['spread']}")
    if not ok:
        return None
    return {"corner": corner, "obs": large, "hit_pct": info["hit_pct"], "rect": rect,
            "how": "seed rect + large stable face verified", "verify": info}


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
    """One short line per search region (strict/relaxed face hits, % of frames) for logs and the run summary."""
    if not dg or dg.get("error"):
        return ["cam diagnostics failed: " + str((dg or {}).get("error"))]
    rows = [f"{c}: strict {v['strict']}/{dg['frames']} ({v.get('strict_pct')}%), relaxed {v['relaxed']}, "
            f"max face {v['max_face_px']} px"
            for c, v in dg.get("corners", {}).items()]
    if dg.get("seed"):
        sd = dg["seed"]
        rows.append(f"seed rect {sd['rect']}: strict {sd['strict']}/{dg['frames']} ({sd.get('strict_pct')}%), "
                    f"max face {sd['max_face_px']} px")
    return rows


# ---- the render ----------------------------------------------------------

def build_ass(cw, hook_text, use_cam, rect):
    """ASS for the clip. With the cam block on top every caption line is centred on the seam (y=540); the
    layout must be known first, which is why the render calls this after the cam check."""
    lane = caption_lane.lanes(use_cam, rect, cam_top=use_cam)
    hook, hook_src, hook_match = hooks.phrase_hook(asr_fix.mask_text(hook_text), cw)
    # every spoken word is captioned: the hook is a separate complete phrase, it never takes words from the captions
    ass = captions.to_ass(cw, 0.0, OUT_W, OUT_H, lane["caption_margin_v"], lane["seam_y"])
    ass = hooks.add_hook(ass, hook, lane["hook_margin_v"])
    return ass, lane, (hook, hook_src, hook_match)


def caption_check(media, s_f, dur, cw_unmasked, src_w, src_h, avoid_px, model, cache_dir, log=print):
    """Rescue ASR + Gemini audio text + OCR of the game's subtitles on the FINAL cut -> (verified words, check)."""
    box = []

    def model_fn():
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model
        path = download_model(model, cache_dir=cache_dir)
        box.append(WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 2))
        return caption_sources.whisper_fn(box[0])
    try:
        return captions_pass.run(media, s_f, dur, cw_unmasked, src_w, src_h, model_fn=model_fn, avoid_px=avoid_px,
                                 key=os.environ.get("GEMINI_API_KEY"), log=log)
    finally:
        box.clear()
        gc.collect()


def render(media, start, end, out_dir, hook_text="", cam_corner=None, cam_rect=None, words=None,
           model="large-v3", cache_dir=None, preset=config.PRESET, log=print, payoff="", vod_id=""):
    """Returns the render.json dict (also written to out_dir). Raises on anything it cannot do honestly."""
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.monotonic()
    pr = download.probe_media(media)
    src_w, src_h, fps = pr["width"], pr["height"], pr["fps"]
    notes, timings = [], {}
    zones = exclusion.zones_for(exclusion.vod_id_from_url(vod_id), src_w, src_h)
    if zones:
        log("overlay zones for this stream: " + ", ".join(sorted(zones)))
        if not cam_rect and (cam_corner or "").strip().lower() != "none":
            cam_rect = exclusion.cam_seed_frac(zones, src_w, src_h)     # seed; the face check below verifies it
            log(f"cam seed from the stream's cam zone: {cam_rect}")
    # A3: nothing given and no known VOD -> the generic detector decides the cam, chat and overlay zones (or the render refuses with
    # its reasons: no guess). cam_rect / known zones / a named corner keep the legacy path. out/cam_find.jpg + .json on every run.
    gen = camresolve.resolve(media, start, max(min(end, pr["duration"] - 0.05) - start, 1.0), src_w, src_h, out_dir, zones, cam_rect,
                             cam_corner, log)
    generic = gen["generic"]
    if generic:
        cam_rect, zones = gen["cam_rect"], gen["zones"]
        log("overlay zones found in the picture: " + ", ".join(sorted(zones)))
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
        s_c, e_c, trim_info = plan_cut(media, pr, start + shift, end, words, cam_corner, cam_rect, payoff, notes, log, zones)
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
            cam = gen["cam"] if generic else seeded_cam(media, s_f, dur, cam_rect, src_w, src_h, log)
            cam_search = {"seeded": True, "generic": generic, "verify": cam["verify"] if cam else None}
        use_cam = cam is not None
        if not use_cam and (mode != "none" or zones):
            raise ValueError("no facecam verified (a large, stable face in the cam region on the sampled frames): "
                             "refusing the clip, there is no full-frame fallback")
        rect = (tuple(cam_rect) if cam_rect else cam["rect"]) if use_cam else None
        cam_region = (gen["region"] if generic else layout.region_px(rect, src_w, src_h)) if use_cam else None
        aspect = layout.PANEL_ASPECT if use_cam else 9 / 16
        pw = layout.panel_wh(src_w, src_h)[0] if use_cam else src_h * 9 / 16
        bounds = panel_bounds(rect, zones, src_w, pw)

        # captions + hook: laid out only now that the layout is known (cam block on top -> captions on the seam)
        ass, lane, (hook, hook_src, hook_match) = build_ass(cw, hook_text, use_cam, rect)
        ass_path = os.path.join(out_dir, "clip.ass")
        with open(ass_path, "w") as f:
            f.write(ass)
        log(f"hook: {hook!r} ({hook_src}, matches first 3 s: {hook_match}); {len(cw)} caption words; "
            f"captions {'on the seam y=' + str(lane['seam_y']) if lane['seam_y'] else 'bottom lane'}")

        # subject track -> smoothed crop path for the gameplay panel
        tmasks = tracking_masks(rect, zones, src_w, src_h)
        dets = [vision.detect_people(f, tmasks) for f in smp["small"]]
        # a subject must be frameable: no wider than the (punched-in) crop and inside the region the crop can reach between the overlays
        pw_eff = pw / (1.0 + (min(config.PUNCH_TARGET, 0.05) if use_cam else config.PUNCH_TARGET))
        m_px = 0.01 * pw_eff + (pw - pw_eff) / 2
        reach = ((bounds[0] + m_px) / src_w, (bounds[1] + pw - m_px) / src_w, 0.98 * pw_eff / src_w)
        sub = subject.build_tracked(dets, vision.small_gray(smp["small"], 240, 135), tmasks, reach=reach)
        cxs = smooth(sub["cx"], alpha=0.4, deadband=0.03)
        pts = [(round(tt, 3), round(max(bounds[0], min(c * src_w - pw / 2, bounds[1])), 1))
               for tt, c in zip(smp["times"], cxs)] or [(0.0, round((bounds[0] + bounds[1]) / 2, 1))]
        xs = per_frame_x(pts, n, fps, bounds)
        zs = punch_curve(n, min(config.PUNCH_TARGET, 0.05) if use_cam else config.PUNCH_TARGET)
        bxs = boxes(xs, zs, src_w, src_h, bounds[0], aspect)
        stats = path_stats(bxs, fps, src_w, src_h, src_h)
        excl = {"checked": False}
        if zones:      # gate: no cam, chat, counter or timer pixels in the gameplay panel (every frame's crop)
            ok_ex, ex_detail, ex_info = exclusion.overlay_gate(bxs, zones)
            excl = {"checked": True, "ok": bool(ok_ex), "detail": ex_detail, **ex_info}
            log(f"overlay gate: {ex_detail} ({ex_info['frames_checked']} frames)")
            if not ok_ex:
                raise ValueError("overlay gate failed: " + ex_detail)
        stats["exclusion"] = excl
        idx = [min(int(round(tt * fps)), n - 1) for tt in smp["times"]]
        sub_plan = subject.inside_stats(sub["box"], smp["times"], [bxs[j][0] for j in idx],
                                        [bxs[j][2] for j in idx], src_w)
        stats["subject"] = sub_plan
        trk = tracking_report(sub, sub_plan, smp["times"], vision.SAMPLE_FPS, src_w=src_w, lefts=[bxs[j][0] for j in idx],
                              widths=[bxs[j][2] for j in idx], bounds=bounds, cxs=cxs)
        sub_plan["sources_pct"] = trk["sources_pct"]
        with open(os.path.join(out_dir, "tracking.json"), "w") as f:
            json.dump(trk, f, indent=1)
        log(f"tracking: subject inside the panel in {sub_plan['in_panel_pct']}% of {sub_plan['samples']} sampled frames "
            f"(gate {TRACK_GATE_PCT:g}%); sources {trk['sources_pct']}; static detections dropped: {len(trk['static_dropped'])}")
        if sub_plan["in_panel_pct"] < TRACK_GATE_PCT:
            raise ValueError(f"tracking gate failed: subject inside the panel in {sub_plan['in_panel_pct']}% of sampled frames "
                             f"(needs >= {TRACK_GATE_PCT:g}%); sources {trk['sources_pct']}; cut at {sub_plan['cut_times'][:12]} "
                             f"(out/tracking.json has every sample)")

        cbx = None
        cam_info = {"found": use_cam, "mode": mode, "search": cam_search}
        if use_cam:
            path = layout.face_path(cam["obs"], n, fps)
            cbx = layout.cam_boxes(path, cam_region, full=generic)
            cam_info.update({"corner": cam["corner"], "face_hit_pct": cam["hit_pct"], "face_w_px": round(path[2], 1),
                             "rect": list(rect), "generic": generic, "region_px": list(cam_region), "box_px": [round(cbx[0][2]), round(cbx[0][3])],
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

    # captions: second sources + rescue pass on the FINAL cut; the ASS is rebuilt from the verified words
    t = time.monotonic()
    avoid = [tuple(z) for z in zones.values()] + ([tuple(cam_region)] if use_cam else [])
    cw_u = [w for w in asr_fix.to_clip_time(words, s_f) if w["s"] < dur]
    for w in cw_u:
        w["e"] = min(w["e"], dur)
    final_u, cap_check = caption_check(media, s_f, dur, cw_u, src_w, src_h, avoid, model, cache_dir, log)
    captions_pass.write_json(cap_check, out_dir)
    timings["captions_check_sec"] = round(time.monotonic() - t, 1)
    if not cap_check["gate"]["ok"]:
        raise ValueError("captions check failed (out/captions_check.json has every word): "
                         + "; ".join(cap_check["gate"]["reasons"]))
    cw = asr_fix.mask_profanity([w for w in final_u if w["s"] < dur])
    ass, lane, (hook, hook_src, hook_match) = build_ass(cw, hook_text, use_cam, rect)
    with open(ass_path, "w") as f:
        f.write(ass)
    log(f"captions verified: {len(cw)} words ({cap_check['counts']['corrected']} corrected, "
        f"{cap_check['counts']['inserted']} inserted), hook {hook!r}")

    # audio: measure, then normalise to -14 LUFS (stream audio only)
    t = time.monotonic()
    p = subprocess.run(loudnorm_measure_cmd(media, s_f, dur, END_FADE), capture_output=True, text=True)
    ln = parse_loudnorm(p.stderr)
    af = loudnorm_filter(dur, ln, END_FADE)
    log(f"audio: input {ln['input_i']:.1f} LUFS, tp {ln['input_tp']:.1f} dBFS -> target {config.TARGET_LUFS:g}")
    timings["loudnorm_sec"] = round(time.monotonic() - t, 1)

    cam_stream, cam_prep, probe = None, None, None
    if use_cam:                         # B3: the cam block comes from the benchmark's chain (never plain lanczos)
        t = time.monotonic()
        b3 = load_b3(out_dir)
        if b3.get("bench_skipped"):
            log("WARNING cam benchmark gate skipped: " + b3["reason"])
            notes.append("cam benchmark skipped (" + b3["reason"] + "); default cam chain, render gates unchanged")
        try:
            up = cam_sr.make(b3["method"]).load()
        except cam_sr.ModelError as e:
            raise ValueError(f"cam enhance: {b3['method']} is unavailable ({e}): refusing the clip, plain lanczos is not shipped")
        cam_stream, cam_prep = cam_enhance.prepare(media, s_f, dur, fps, fps_arg(fps), cbx, tuple(cam_region),
                                                   (src_w, src_h), b3, up, log)
        probe = cam_enhance.new_probe(n)
        timings["cam_prep_sec"] = round(time.monotonic() - t, 1)
    t = time.monotonic()
    out_path = os.path.join(out_dir, "clip.mp4")
    short = render_frames(media, s_f, n, fps, bxs, src_w, src_h,
                          encode_cmd(media, s_f, dur, ass_path, out_path, fps, af, preset=preset),
                          os.path.join(out_dir, "encode.log"), cbx,
                          tuple(cam_region) if use_cam else None, cam_stream, probe)
    timings["render_sec"] = round(time.monotonic() - t, 1)
    audio_final = final_true_peak(media, s_f, dur, out_path, ln, log)
    if not audio_final["ok"]:
        raise ValueError(f"true peak {audio_final['tp']} dBFS is above {config.TRUE_PEAK:g} after "
                         f"{len(audio_final['passes'])} measurements: refusing the clip")

    # A3: the gate on the RENDERED pixels: k frames of the finished clip are traced back into the source; the gameplay panel must
    # come from source columns clear of the cam, chat and overlay zones, the cam block from inside the cam. No trace = no pass.
    rgate = None
    if use_cam:
        t = time.monotonic()
        cam_px = tuple(float(v) for v in (gen["rect_px"] if generic else cam_region))
        gz = {k: tuple(float(v) for v in z) for k, z in (zones or {}).items()}
        gz.setdefault("cam", cam_px)
        pws = [OUT_W / b[2] for b in bxs]
        panel_scales = [float(v) for v in np.linspace(min(pws) * 0.97, max(pws) * 1.03, 9)]
        cam_scales = [float(OUT_W / cbx[0][2] * f) for f in (0.94, 0.97, 1.0, 1.03, 1.06)]
        rgate = camfind.rendered_gate(media, out_path, s_f, dur, src_w, src_h, gz, cam_px, panel_scales, cam_scales)
        timings["rendered_gate_sec"] = round(time.monotonic() - t, 1)
        log(f"rendered gate: {'PASS' if rgate['ok'] else 'FAIL'} on {len(rgate.get('frames') or [])} frames"
            + ("" if rgate["ok"] else ": " + "; ".join(rgate["reasons"])))
    cam_quality = cam_enhance.quality_report(cam_stream, cam_prep, probe) if cam_stream is not None else None
    if probe and probe["pairs"]:
        n_cmp = cam_enhance.write_compare(probe, os.path.join(out_dir, "cam_compare.png"),
                                          f"NEW: {cam_quality['chain'][:80]}")
        log(f"cam_compare.png: {n_cmp} moments, old chain over new chain, real 1080x540")
    source_format = srcinfo.media_info(media)
    clip_file = clip_file_info(out_path)
    if cam_quality:
        timings["cam_sr_sec"] = cam_quality["sr_seconds"]
        log(f"cam quality: fidelity {cam_quality['fidelity']} blockiness {cam_quality['blockiness']} edge {cam_quality['edge']} "
            f"(plain lanczos {cam_quality['lanczos']}) gate {'PASS' if cam_quality['gate']['ok'] else 'FAIL'}")
    r = {"source": {"width": src_w, "height": src_h, "fps": fps, "duration": pr["duration"]},
         "asked": {"start": start, "end": end}, "cut": {"start": s_f, "end": round(s_f + dur, 3), "duration": round(dur, 4), "frames": n},
         "expected": {"frames": n, "duration": round(dur, 4), "source_fps": fps}, "short_frames": short,
         "hook": {"text": hook, "source": hook_src, "match": hook_match}, "trim": trim_info, "words": len(cw), "asr": meta, "notes": notes,
         "word_spans": [[round(w["s"], 3), round(w["e"], 3)] for w in cw],
         "source_format": source_format, "clip_file": clip_file,
         "cam_diag": diag, "crop_points": pts, "rendered_gate": rgate,
         "cam_find": {"generic": generic, "rect_px": list(gen["rect_px"]) if generic else None,
                      "region_px": list(cam_region) if use_cam else None,
                      "confidence": gen["detect"]["confidence"] if generic else None, "zones": {k: list(v) for k, v in (zones or {}).items()}},
         "cam_scale": {"upscale": cam_info.get("upscale"), "box_px": cam_info.get("box_px"),
                       "chain": cam_quality["chain"] if cam_quality else "none (no cam block)"}, "cam_quality": cam_quality, "loudnorm_input": ln, "audio_final": audio_final, "timings": timings,
         "encode": {"codec": "libx264", "crf": config.CRF, "preset": preset},
         "captions_check": captions_pass.summary(cap_check),
         "grade_inputs": {"captions_check": captions_pass.summary(cap_check), "cam_diag": diag, "audio_inputs": 1, "audio_filters": list(AUDIO_FILTERS), "trim": trim_info,
                          "hook": {"text": hook, "source": hook_src, "match": hook_match}, "cam_quality": cam_quality,
                          "rendered_gate": rgate, **stats}}
    with open(os.path.join(out_dir, "render.json"), "w") as f:
        json.dump(r, f, indent=1)
    lj = layout.layout_report(src_w, src_h, rect, cam_info, bxs, stats.get("exclusion"))
    lj["cam_find"] = r["cam_find"]
    lj["rendered_gate"] = rgate
    with open(os.path.join(out_dir, "layout.json"), "w") as f:
        json.dump(lj, f, indent=1)
    timings["total_sec"] = round(time.monotonic() - t0, 1)
    log(f"rendered {out_path} in {timings['render_sec']}s (total {timings['total_sec']}s)")
    if rgate is not None and not rgate["ok"]:      # A3 gate: render.json / layout.json (with the numbers) are on disk, now refuse
        raise ValueError("rendered output gate failed (cam/chat/overlay pixels in the gameplay panel, or the cam block is not the cam): "
                         + "; ".join(rgate["reasons"]))
    if cam_quality:                     # B3 gate: the diagnostics above are already on disk; now refuse a clip that fails it
        q_ok, q_reasons = cam_sr.quality_verdict(cam_quality)
        if not q_ok:
            raise ValueError("cam quality gate failed (the enhanced cam block does not beat plain lanczos): " + "; ".join(q_reasons))
    return r
