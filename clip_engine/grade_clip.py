"""Measure a rendered clip with ffprobe/ffmpeg and grade it against the style rules."""
import json
import os
import re
import subprocess
from fractions import Fraction

import numpy as np

from . import analyze, balance, gates, grade_checks, subject, transcribe
from .captions import HL_ON

REVIEW_TIMES = (0.3, 0.9, 4.0, 10.0, 18.0, 26.0)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _fps(s):
    try:
        return float(Fraction(s))
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path):
    p = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    d = json.loads(p.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    n = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
              "stream=nb_read_frames", "-of", "csv=p=0", path]).stdout.strip()
    dur_f = float(v.get("duration") or d["format"]["duration"])
    br = int(v.get("bit_rate") or 0) or int(int(d["format"].get("size", 0)) * 8 / max(dur_f, 0.001))
    video = {"width": v.get("width"), "height": v.get("height"), "codec": v.get("codec_name"),
             "pix_fmt": v.get("pix_fmt"), "fps": round(_fps(v.get("r_frame_rate", "0")), 3),
             "bit_rate_kbps": round(br / 1000), "color_range": v.get("color_range"),
             "color_space": v.get("color_space"), "color_transfer": v.get("color_transfer"),
             "color_primaries": v.get("color_primaries"),
             "frames": int(n) if n.isdigit() else None,
             "duration": round(float(v.get("duration") or d["format"]["duration"]), 3)}
    audio = None
    if a:
        audio = {"codec": a.get("codec_name"), "sample_rate": int(a.get("sample_rate", 0)),
                 "duration": round(float(a.get("duration") or d["format"]["duration"]), 3)}
    return video, audio, int(d["format"].get("size", 0))


def crf_of(path, scan=600_000):
    with open(path, "rb") as f:
        m = re.search(rb"crf=([0-9.]+)", f.read(scan))
    return float(m.group(1)) if m else None


def parse_ebur128(text):
    tail = text.split("Summary:")[-1]
    g = lambda pat: (lambda m: float(m.group(1)) if m else None)(re.search(pat, tail))
    return {"i": g(r"I:\s+(-?[\d.]+|-inf) LUFS"), "lra": g(r"LRA:\s+([\d.]+) LU"),
            "tp": g(r"Peak:\s+(-?[\d.]+|-inf) dBFS")}


def loudness(path):
    p = _run(["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-vn", "-af", "ebur128=peak=true",
              "-f", "null", "-"])
    return parse_ebur128(p.stderr)


def gray_frames(path, fps, w, h):
    p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-an", "-vf",
                        f"fps={fps},scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True)
    buf = np.frombuffer(p.stdout, dtype=np.uint8)
    n = len(buf) // (w * h)
    return buf[:n * w * h].reshape(n, h, w)


def _t(s):
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def parse_ass(text):
    rows = [l[len("Dialogue:"):].strip().split(",", 4) for l in text.splitlines() if l.startswith("Dialogue:")]
    hook = [r for r in rows if r[3] == "Hook"]
    cap = [r for r in rows if r[3] != "Hook"]
    return {"hook_start": _t(hook[0][1]) if hook else None, "hook_text": hook[0][4] if hook else "",
            "caption_styles": sorted({r[3] for r in cap}), "caption_lines": len(cap),
            "max_highlights": max((r[4].count(HL_ON) for r in cap), default=0),
            "last_end": max((_t(r[2]) for r in cap), default=0.0)}


def subject_output(clip, y0, h, fps=2, w_out=540):
    """Re-detect the person in the RENDERED gameplay panel (independent of the render-time tracker)."""
    from . import vision
    hh = int(round(h * w_out / 1080 / 2) * 2)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", clip, "-an", "-vf",
           f"fps={fps},crop=1080:{h}:0:{y0},scale={w_out}:{hh}:flags=area", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.run(cmd, capture_output=True)
    size = w_out * hh * 3
    n = len(p.stdout) // size
    dets, times = [], []
    for i in range(n):
        im = np.frombuffer(p.stdout[i * size:(i + 1) * size], np.uint8).reshape(hh, w_out, 3)
        dets.append([(b[0] * w_out, b[1] * hh, b[2] * w_out, b[3] * hh, b[4]) for b in vision.detect_people(im)])
        times.append(i / fps)
    return subject.summarize_output(subject.classify_output(dets, w_out), times)


def review_frames(path, out_dir, duration, times=REVIEW_TIMES):
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for i, t in enumerate([x for x in times if x < duration - 0.2] + [max(duration - 0.4, 0)]):
        o = os.path.join(out_dir, f"r{i}_{t:05.1f}s.jpg")
        _run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
              "-vf", "scale=540:-2", "-q:v", "3", o])
        if os.path.exists(o):
            made.append(o)
    return made


def grade(clip, render_json, ass_path, out_dir):
    """Returns (measures, checks). Writes grade.json and review frames into out_dir."""
    r = json.load(open(render_json))
    video, audio, size = probe(clip)
    m = {"video": video, "audio": audio, "size_mb": round(size / 1e6, 1), "crf": crf_of(clip),
         "loud": loudness(clip), "expected": r["expected"], "short_frames": r.get("short_frames", 0),
         "render": r["grade_inputs"], "ass": parse_ass(open(ass_path).read())}
    m["visual"] = dict(zip(("ok", "detail"), gates.visual_gate(gray_frames(clip, 2, 108, 192))))
    series = analyze.motion_series(gray_frames(clip, 4, 54, 96), fps=4)
    m["motion"] = dict(zip(("ok", "detail"), gates.motion_gate(series)))
    try:
        pan = r["grade_inputs"].get("panel") or {"y0": 0, "h": 1920}
        m["subject_out"] = subject_output(clip, pan["y0"], pan["h"])
    except Exception as e:      # cv2 missing etc.: say so, never pass by default
        m["subject_out"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    try:
        pcm = transcribe.decode_pcm(clip, 0.0, video["duration"])
        m["balance"] = balance.balance(pcm, transcribe.SR, r.get("word_spans", []))
    except Exception as e:
        m["balance"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    checks = grade_checks.run_all(m)
    m["review_frames"] = review_frames(clip, os.path.join(out_dir, "review"), video["duration"])
    with open(os.path.join(out_dir, "grade.json"), "w") as f:
        json.dump({"measures": m, "checks": checks}, f, indent=1)
    return m, checks
