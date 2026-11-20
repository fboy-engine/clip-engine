"""yt-dlp wrapper: proxy + cookies + player-client fallback, with log redaction.

Every line yt-dlp prints goes through redact(), so cookies and signed
googlevideo URLs never reach a log. Runner-agnostic: reads YT_PROXY and
YT_COOKIES_FILE from the environment; nothing GitHub-specific in here.
"""
import glob
import json
import os
import re
import subprocess
import threading

MAX_ATTEMPTS = 3            # never a retry storm
DEFAULT_CLIENTS = (None, "tv", "mweb")


class DownloadFailed(Exception):
    pass


_RULES = [
    (re.compile(r"(?im)^(\s*(?:set-)?cookie\s*:).*$"), r"\1 <redacted>"),
    (re.compile(r"[^\s\t]+\t(?:TRUE|FALSE)\t[^\t\n]*\t(?:TRUE|FALSE)\t\d+\t[^\t\n]+\t[^\t\n]*"),
     "<redacted-cookie-line>"),
    (re.compile(r"https?://[^\s'\"<>]*googlevideo\.com[^\s'\"<>]*", re.I), "<redacted-url>"),
    (re.compile(r"([?&](?:sig|lsig|signature|key|token|expire|ip|ipbits|sparams|pot|po_token|n)=)"
                r"[^&\s'\"]+", re.I), r"\1<redacted>"),
    (re.compile(r"\b(SAPISID|APISID|SSID|HSID|SID|SIDCC|LOGIN_INFO|VISITOR_INFO1_LIVE|YSC|PREF"
                r"|__Secure-\w+|__Host-\w+)=[^\s;]+"), r"\1=<redacted>"),
    (re.compile(r"[A-Za-z0-9_\-%]{40,}"), "<redacted-token>"),
]


def redact(text):
    for pat, rep in _RULES:
        text = pat.sub(rep, text)
    return text


def common_args(cookies=None, proxy=None, client=None):
    a = ["yt-dlp", "--no-colors", "--no-playlist", "--js-runtimes", "node",
         "--retries", "3", "--fragment-retries", "3", "--socket-timeout", "30"]
    if cookies:
        a += ["--cookies", cookies]
    if proxy:
        a += ["--proxy", proxy]
    if client:
        a += ["--extractor-args", f"youtube:player_client={client}"]
    return a


def run_stream(cmd, timeout=1200):
    """Run, print redacted output line by line, return the exit code."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace")
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    try:
        for line in p.stdout:
            print(redact(line.rstrip()), flush=True)
        return p.wait()
    finally:
        timer.cancel()


def info(url, cookies=None, proxy=None, client=None):
    """Safe metadata only. The raw JSON holds signed URLs and is never printed."""
    cmd = common_args(cookies, proxy, client) + ["--skip-download", "-J", url]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    err = redact(p.stderr or "")
    if p.returncode != 0:
        return None, err
    d = json.loads(p.stdout)
    heights = [f.get("height") or 0 for f in d.get("formats", [])]
    safe = {k: d.get(k) for k in ("id", "title", "channel", "live_status", "is_live",
                                  "was_live", "duration")}
    safe["max_height"] = max(heights or [0])
    return safe, err


def fetch_window(url, start, end, out_dir, cookies=None, proxy=None,
                 clients=DEFAULT_CLIENTS, runner=run_stream):
    """Download only [start, end] seconds of a stream (ended, or the DVR window of a
    live one) as mp4. Stream copy: NO --force-keyframes-at-cuts, which made yt-dlp
    re-encode the section to MPEG-2 (run 44807660941); cuts land on keyframes, so
    the file can run a few seconds longer than asked.
    Returns (path, client_used). Tries at most MAX_ATTEMPTS player clients."""
    os.makedirs(out_dir, exist_ok=True)
    last = None
    for client in list(clients)[:MAX_ATTEMPTS]:
        cmd = common_args(cookies, proxy, client) + [
            "-S", "res:1440,fps", "-f", "bv*+ba/b",
            "--download-sections", f"*{start:g}-{end:g}",
            "--merge-output-format", "mp4", "--concurrent-fragments", "4",
            "--progress-delta", "15", "--newline",
            "-o", os.path.join(out_dir, "source.%(ext)s"), url]
        rc = runner(cmd, 1200)
        files = sorted(glob.glob(os.path.join(out_dir, "source.mp4")))
        if rc == 0 and files:
            return files[0], client
        last = rc
        print(f"download attempt with client={client or 'default'} failed (rc={last})", flush=True)
    raise DownloadFailed(f"yt-dlp failed after {MAX_ATTEMPTS} attempts (last rc={last})")


def probe_media(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                          "-show_streams", "-show_format", path],
                         capture_output=True, text=True).stdout
    d = json.loads(out)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    num, _, den = (v.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {"width": v["width"], "height": v["height"], "fps": round(fps, 2),
            "vcodec": v["codec_name"],
            "has_audio": any(s["codec_type"] == "audio" for s in d["streams"]),
            "duration": round(float(d["format"]["duration"]), 2),
            "size_mb": round(int(d["format"]["size"]) / 1e6, 1)}
