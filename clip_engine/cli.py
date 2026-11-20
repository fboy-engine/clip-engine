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
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
