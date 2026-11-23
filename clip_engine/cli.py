"""Plain Python CLI. Runner-agnostic: no GitHub-specific code in here.

Usage: python -m clip_engine.cli <command> [args]
"""
import argparse
import json
import os
import sys

from . import download, gemini


def gemini_check(_a):
    """Walk the candidate chain until ONE model answers. Never prints the key."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("FAIL: GEMINI_API_KEY is not set in this environment")
        return 1
    timeout = int(os.environ.get("GEMINI_PROBE_TIMEOUT", "20"))
    try:
        names = gemini.list_models(key)
    except Exception as e:
        print(f"FAIL: ListModels: {type(e).__name__}")
        return 1
    flashy = sorted(n.split("/", 1)[-1] for n in names if "flash" in n)
    print("flash-ish models: " + (", ".join(flashy) or "none"))
    chain = gemini.candidates(names)
    print("chain: " + ", ".join(chain))
    for m in chain:
        ok, detail, secs = gemini.probe(key, m, timeout=timeout)
        print(f"{'OK  ' if ok else 'FAIL'} {m}: {detail} ({secs:.1f}s)")
        if ok:
            print(f"PASS (use GEMINI_MODEL={m} to pin)")
            return 0
    print("FAIL: no model in the chain answered")
    return 1


def transcribe_default_cache():
    return os.environ.get("CLIP_MODEL_CACHE", os.path.expanduser("~/.cache/clip-engine/models"))


def _env():
    return os.environ.get("YT_COOKIES_FILE"), os.environ.get("YT_PROXY")


def cmd_check_cv(_a):
    """Fail fast: the render needs OpenCV 4.x with the face cascade and HOG people detector."""
    try:
        from . import vision
        ok, lines = vision.check_features()
    except Exception as e:  # import error etc.: say so, never pass by default
        print(f"FAIL check-cv: {type(e).__name__}: {str(e)[:300]}")
        return 1
    print("\n".join(lines))
    print("PASS check-cv" if ok else "FAIL check-cv: fix the OpenCV install before rendering")
    return 0 if ok else 1


def cmd_info(a):
    cookies, proxy = _env()
    safe, err = download.info(a.url, cookies, proxy)
    if err.strip():
        print(err.strip()[-1500:])
    if safe is None:
        print("FAIL: yt-dlp could not read the stream")
        return 1
    print(json.dumps(safe, indent=1))
    return 0


def cmd_fetch_twitch(a):
    """Twitch clip: the whole clip, anonymous (no cookies, no proxy), start/end are not used."""
    try:
        path = download.fetch_twitch_clip(a.url, a.out)
    except download.DownloadFailed as e:
        print(f"FAIL: {e}")
        return 1
    summary = download.probe_media(path)
    summary["player_client"] = "twitch clip (anonymous)"
    summary["via_proxy"] = False
    with open(os.path.join(a.out, "probe.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print("PASS download: " + json.dumps(summary))
    return 0


def cmd_fetch(a):
    if download.is_twitch(a.url):
        return cmd_fetch_twitch(a)
    # live streams use the same path: the section is cut from the DVR window
    if not a.end > a.start >= 0:
        print("FAIL: need end_sec > start_sec >= 0")
        return 2
    cookies, proxy = _env()
    try:
        path, client = download.fetch_window(a.url, a.start, a.end, a.out, cookies, proxy)
    except download.DownloadFailed as e:
        print(f"FAIL: {e}")
        return 1
    summary = download.probe_media(path)
    if summary["vcodec"] in ("mpeg2video", "mpeg1video"):
        print("FAIL: source was re-encoded to " + summary["vcodec"] + ", refusing to continue")
        return 1
    summary["player_client"] = client or "default"
    summary["via_proxy"] = bool(proxy)
    with open(os.path.join(a.out, "probe.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print("PASS download: " + json.dumps(summary))
    return 0


def cmd_transcribe(a):
    from . import transcribe
    try:
        m, segs = transcribe.run(a.media, a.model, a.start, a.end or None, a.max_sec or None,
                                 a.language, a.beam, a.cache_dir, a.out)
    except Exception as e:  # report the real failure, never fake a pass
        print(f"FAIL transcribe {a.model}: {type(e).__name__}: {str(e)[:300]}")
        return 1
    print("METRICS " + json.dumps(m))
    text = " ".join(s["text"] for s in segs)
    print(f"words={m['n_words']} sample: {text[:300]}")
    if m["n_words"] == 0:
        print("FAIL transcribe: zero words")
        return 1
    print(f"PASS transcribe {a.model}")
    return 0


def cmd_find(a):
    from . import findrun
    try:
        return findrun.run(a.media, a.offset, a.model, a.top, a.out, a.cache_dir)
    except Exception as e:  # report the real failure, never fake a pass
        print(f"FAIL find: {type(e).__name__}: {str(e)[:300]}")
        return 1


def _grade_out(out):
    from . import grade_checks, grade_clip
    p = lambda n: os.path.join(out, n)
    m, checks = grade_clip.grade(p("clip.mp4"), p("render.json"), p("clip.ass"), out)
    text = grade_checks.key_numbers(m) + "\n\n" + grade_checks.format_report(checks)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("### Clip grade\n```\n" + text + "\n```\n")
    return 1 if grade_checks.failed(checks) else 0


def _step_summary(text):
    """Print the text and append it to the Actions run summary (nothing for an empty text)."""
    if not text:
        return
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(text)


def cmd_render(a):
    from . import render_clip
    from . import window
    rect = [float(x) for x in a.cam_rect.split(",")] if a.cam_rect else None
    try:
        window.check_window(a.start, a.end)
        res = render_clip.render(a.media, a.start, a.end, a.out, a.hook, a.cam_corner, rect,
                                 model=a.model, cache_dir=a.cache_dir, payoff=a.payoff, vod_id=a.vod_id)
        _step_summary(render_clip.summary_md(res or {}))
    except Exception as e:  # report the real failure, never fake a pass
        msg = f"FAIL render: {type(e).__name__}: {str(e)[:900]}"
        print(msg)
        lines = [msg]
        try:    # cam diagnostics were written before the gates ran: report them even though no clip exists
            os.makedirs(a.out, exist_ok=True)
            with open(os.path.join(a.out, "render_error.json"), "w") as f:
                json.dump({"error": msg}, f, indent=1)
            with open(os.path.join(a.out, "cam_diag.json")) as f:
                lines += ["cam diagnostics (face hits per corner, % of frames):"] + render_clip.diag_lines(json.load(f))
        except Exception:
            pass
        print("\n".join(lines[1:]))
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                f.write("### Clip render FAILED (no clip)\n```\n" + "\n".join(lines) + "\n```\n")
        try:    # a gate refusal leaves render.json behind: its numbers are the evidence
            with open(os.path.join(a.out, "render.json")) as f:
                _step_summary(render_clip.summary_md(json.load(f)))
        except Exception:
            pass
        return 1
    return _grade_out(a.out)


def cmd_cam_bench(a):
    """Benchmark the cam upscalers on the real cam crops of the cached source (no download of media)."""
    from . import cam_bench, cam_sr, window
    cookies, proxy = _env()
    inputs = {"start": a.start, "end": a.end, "vod_id": a.vod_id}
    try:
        window.check_window(a.start, a.end)
        _, md = cam_bench.run(a.media, a.start, a.end, a.out, vod_id=a.vod_id, url=a.url if a.formats else "",
                              cache_dir=a.cache_dir or None, cookies=cookies, proxy=proxy,
                                  candidates=tuple(c for c in a.candidates.split(",") if c) or cam_sr.CANDIDATES)
    except cam_bench.NotEnoughCrops as e:                # not a render blocker: warn, skip the benchmark gate, the render goes on
        warn = f"WARNING cam-bench skipped: {str(e)[:500]}. The render uses the default cam chain; its own gates still apply."
        print(warn)
        os.makedirs(a.out, exist_ok=True)
        with open(os.path.join(a.out, "cam_bench.json"), "w") as f:
            json.dump({"skipped": True, "warning": warn, "inputs": inputs}, f, indent=1)
        with open(os.path.join(a.out, "cam_bench.md"), "w") as f:
            f.write("### Cam benchmark SKIPPED\n" + warn + "\n")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                f.write("### Cam benchmark SKIPPED (warning, render continues)\n```\n" + warn + "\n```\n")
        return 0
    except Exception as e:
        msg = f"FAIL cam-bench: {type(e).__name__}: {str(e)[:600]}"
        print(msg)
        window.write_error_report(a.out, msg, inputs)      # the artifact upload always has something
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                f.write("### Cam benchmark FAILED\n```\n" + msg + "\n```\n")
        return 1
    print(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(md)
    return 0


def cmd_grade(a):
    try:
        return _grade_out(a.out)
    except Exception as e:
        print(f"FAIL grade: {type(e).__name__}: {str(e)[:400]}")
        return 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="clip_engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gemini-check").set_defaults(fn=gemini_check)
    sub.add_parser("check-cv").set_defaults(fn=cmd_check_cv)
    i = sub.add_parser("info")
    i.add_argument("url")
    i.set_defaults(fn=cmd_info)
    f = sub.add_parser("fetch")
    f.add_argument("url")
    f.add_argument("--start", type=float, required=True)
    f.add_argument("--end", type=float, required=True)
    f.add_argument("--out", default="out")
    f.add_argument("--live", action="store_true")
    f.set_defaults(fn=cmd_fetch)
    t = sub.add_parser("transcribe")
    t.add_argument("media")
    t.add_argument("--model", default="small")
    t.add_argument("--start", type=float, default=0.0)
    t.add_argument("--end", type=float, default=0.0)
    t.add_argument("--max-sec", dest="max_sec", type=float, default=0.0)
    t.add_argument("--language", default=None)
    t.add_argument("--beam", type=int, default=1)
    t.add_argument("--cache-dir", dest="cache_dir", default=transcribe_default_cache())
    t.add_argument("--out", default="out")
    t.set_defaults(fn=cmd_transcribe)
    fd = sub.add_parser("find")
    fd.add_argument("media")
    fd.add_argument("--offset", type=float, default=0.0, help="stream second where the media starts")
    fd.add_argument("--model", default="small")
    fd.add_argument("--top", type=int, default=8)
    fd.add_argument("--cache-dir", dest="cache_dir", default=transcribe_default_cache())
    fd.add_argument("--out", default="out")
    fd.set_defaults(fn=cmd_find)
    r = sub.add_parser("render")
    r.add_argument("media")
    r.add_argument("--start", type=float, required=True, help="clip start, seconds in the media file")
    r.add_argument("--end", type=float, required=True)
    r.add_argument("--hook", default="")
    r.add_argument("--cam-corner", dest="cam_corner", default=None)
    r.add_argument("--cam-rect", dest="cam_rect", default=None, help="x0,y0,x1,y1 fractions")
    r.add_argument("--payoff", default="", help="phrase the clip should end on (fuzzy match in the transcript)")
    r.add_argument("--vod-id", dest="vod_id", default="", help="stream URL or 11-char id: selects its overlay zones")
    r.add_argument("--model", default="large-v3")
    r.add_argument("--cache-dir", dest="cache_dir", default=transcribe_default_cache())
    r.add_argument("--out", default="out")
    r.set_defaults(fn=cmd_render)
    cb = sub.add_parser("cam-bench")
    cb.add_argument("media")
    cb.add_argument("--start", type=float, required=True, help="window start, seconds in the media file")
    cb.add_argument("--end", type=float, required=True)
    cb.add_argument("--vod-id", dest="vod_id", default="")
    cb.add_argument("--url", default="", help="stream URL: only used to LIST formats (metadata), never to download")
    cb.add_argument("--formats", action="store_true", help="also list the platform's formats (metadata only)")
    cb.add_argument("--cache-dir", dest="cache_dir", default="")
    cb.add_argument("--candidates", default="", help="comma list: lanczos,fsrcnn,edsr,realesrgan (empty = all of cam_sr.CANDIDATES)")
    cb.add_argument("--out", default="out")
    cb.set_defaults(fn=cmd_cam_bench)
    g = sub.add_parser("grade")
    g.add_argument("--out", default="out")
    g.set_defaults(fn=cmd_grade)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
