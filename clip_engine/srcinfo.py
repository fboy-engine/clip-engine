"""Format and bitrate of the source video (and of the formats the platform offers), for the run summary."""
import json
import subprocess
from fractions import Fraction


def media_info(path):
    """ffprobe summary of a media file: codec, profile, pix_fmt, size, fps, video bitrate, container, audio."""
    p = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
                       capture_output=True, text=True)
    d = json.loads(p.stdout)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
    fmt = d.get("format", {})
    dur = float(v.get("duration") or fmt.get("duration") or 0)
    size = int(fmt.get("size") or 0)
    vbr = int(v.get("bit_rate") or 0)
    est = False
    if not vbr and dur > 0:       # mkv/webm often carry no per-stream rate: whole file minus audio, estimated
        vbr = int(size * 8 / dur) - int(a.get("bit_rate") or 0)
        est = True
    try:
        fps = round(float(Fraction(v.get("avg_frame_rate") or v.get("r_frame_rate") or "0")), 3)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    return {"container": fmt.get("format_name"), "vcodec": v.get("codec_name"), "profile": v.get("profile"),
            "pix_fmt": v.get("pix_fmt"), "width": v.get("width"), "height": v.get("height"), "fps": fps,
            "video_kbps": round(vbr / 1000) if vbr else None, "video_kbps_estimated": est,
            "acodec": a.get("codec_name"), "audio_kbps": round(int(a.get("bit_rate") or 0) / 1000) or None,
            "duration": round(dur, 2), "size_mb": round(size / 1e6, 2),
            "color_range": v.get("color_range"), "color_space": v.get("color_space")}


def info_line(i):
    br = f"{i['video_kbps']} kbps" + (" (estimated)" if i.get("video_kbps_estimated") else "") if i.get("video_kbps") else "bitrate unknown"
    return (f"{i.get('vcodec')} {i.get('profile') or ''} {i.get('pix_fmt')} {i.get('width')}x{i.get('height')} "
            f"{i.get('fps')} fps, {br}, container {i.get('container')}").replace("  ", " ")


def format_list(url, cookies=None, proxy=None, runner=subprocess.run):
    """Metadata only (yt-dlp -J --skip-download): the video formats the platform offers. NEVER downloads media.
    Returns (list, error); each item has id, ext, vcodec, height, fps, tbr. Signed URLs are not kept."""
    from . import download
    cmd = download.common_args(cookies, proxy, None) + ["--skip-download", "-J", url]
    try:
        p = runner(cmd, capture_output=True, text=True, timeout=90)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"
    if p.returncode != 0:
        return None, download.redact(p.stderr or "")[-300:]
    d = json.loads(p.stdout)
    out = []
    for f in d.get("formats", []):
        if (f.get("vcodec") or "none") == "none":
            continue
        out.append({"id": f.get("format_id"), "ext": f.get("ext"), "vcodec": (f.get("vcodec") or "")[:12],
                    "height": f.get("height"), "fps": f.get("fps"), "tbr": round(f.get("tbr") or 0)})
    return out, ""


def better_formats(cached, formats):
    """Formats clearly better than what is cached: taller, or the same height with a >= 25 % higher bitrate.
    The report lists them; the person decides (nothing is downloaded here)."""
    h, br = cached.get("height") or 0, cached.get("video_kbps") or 0
    res = []
    for f in formats or []:
        fh, ft = f.get("height") or 0, f.get("tbr") or 0
        if fh > h or (fh == h and br and ft >= br * 1.25):
            res.append(f)
    res.sort(key=lambda f: (-(f.get("height") or 0), -(f.get("tbr") or 0)))
    return res[:6]
