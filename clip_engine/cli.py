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


def cmd_fetch(a):
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
    text = grade_checks.format_report(checks)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("### Clip grade\n```\n" + text + "\n```\n")
    return 1 if grade_checks.failed(checks) else 0


def cmd_render(a):
    from . import render_clip
    rect = [float(x) for x in a.cam_rect.split(",")] if a.cam_rect else None
    try:
        render_clip.render(a.media, a.start, a.end, a.out, a.hook, a.cam_corner, rect,
                           model=a.model, cache_dir=a.cache_dir)
    except Exception as e:  # report the real failure, never fake a pass
        print(f"FAIL render: {type(e).__name__}: {str(e)[:400]}")
        return 1
    return _grade_out(a.out)


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
    r.add_argument("--model", default="large-v3")
    r.add_argument("--cache-dir", dest="cache_dir", default=transcribe_default_cache())
    r.add_argument("--out", default="out")
    r.set_defaults(fn=cmd_render)
    g = sub.add_parser("grade")
    g.add_argument("--out", default="out")
    g.set_defaults(fn=cmd_grade)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
