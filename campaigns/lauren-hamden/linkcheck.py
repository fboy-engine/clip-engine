#!/usr/bin/env python3
"""B1: metadata-only probe of every Lauren Hamden campaign source (nothing is downloaded).

Prints one line per source and a JSON report to out/linkcheck.json.
Twitch needs no cookies and no WARP.
"""
import json
import os
import subprocess
import sys

BASE = "https://m.twitch.tv/"
SOURCES = [
    (1, "clip", BASE + "laurenhamdenn/clip/GoldenSincereHamburgerHeyGuys-Zo31y1z1tLdwRScS"),
    (3, "clip", BASE + "laurenhamdenn/clip/MuddyTardyClipzFeelsBadMan-Qb1_guoZe76pMLRY"),
    (4, "clip", BASE + "laurenhamdenn/clip/SlickPatientToadDoritosChip-LV7_h3WaUULTckLh"),
    (5, "clip", BASE + "laurenhamdenn/clip/PeacefulBraveBubbleteaOhMyDog-smiMxfhYlj3rkpNB"),
    (6, "clip", BASE + "laurenhamdenn/clip/CogentHeadstrongSashimiPanicVis-U3eSLSv9jOYbOnIs"),
    (7, "clip", BASE + "laurenhamdenn/clip/AttractiveTriumphantSmoothieTebowing-cXFFO-s8cKYJpIWg"),
    (8, "clip", BASE + "laurenhamdenn/clip/TallSarcasticDumplingsRalpherZ-yfcKexOzQP153QQ6"),
    (9, "clip", BASE + "laurenhamdenn/clip/OptimisticFantasticVanillaRickroll-h3S4-Q874oTvgVNG"),
    (10, "vod", BASE + "videos/2934045907"),
]


def probe(url):
    cmd = ["yt-dlp", "--no-colors", "--no-playlist", "--skip-download", "-J", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout after 180 s"}
    if p.returncode != 0:
        lines = [l for l in p.stderr.strip().splitlines() if l.strip()]
        return {"ok": False, "error": (lines[-1] if lines else "unknown error")[:300]}
    try:
        j = json.loads(p.stdout)
    except ValueError as e:
        return {"ok": False, "error": "bad JSON: %s" % e}
    fmts = j.get("formats") or []
    audio_only = [f.get("format_id") for f in fmts if f.get("vcodec") == "none"]
    heights = sorted({f.get("height") for f in fmts if f.get("height")})
    return {
        "ok": True,
        "title": j.get("title"),
        "duration_s": j.get("duration"),
        "uploader": j.get("uploader"),
        "upload_date": j.get("upload_date"),
        "is_live": j.get("is_live"),
        "fps": max([f.get("fps") or 0 for f in fmts] or [0]),
        "heights": heights,
        "audio_only_formats": audio_only,
        "n_formats": len(fmts),
    }


def main():
    rows = []
    for n, kind, url in SOURCES:
        r = probe(url)
        r.update({"n": n, "kind": kind, "url": url})
        rows.append(r)
        if r["ok"]:
            d = r.get("duration_s")
            note = ""
            if kind == "clip" and d is not None and d < 25:
                note = " SHORT SOURCE (<25 s)"
            if kind == "vod":
                note = " audio-only formats: %s" % (r["audio_only_formats"] or "NONE")
            print("OK   #%d %s %ss max %sp %sfps live=%s%s" % (
                n, kind, d, (r["heights"] or [None])[-1], r["fps"], r["is_live"], note))
            print("       title=%r by %s on %s" % (r["title"], r["uploader"], r["upload_date"]))
        else:
            print("DEAD #%d %s %s" % (n, kind, r["error"]))
    os.makedirs("out", exist_ok=True)
    with open("out/linkcheck.json", "w") as f:
        json.dump(rows, f, indent=2)
    dead = [r["n"] for r in rows if not r["ok"]]
    print("SUMMARY ok=%d dead=%s" % (len(rows) - len(dead), dead))
    return 0


if __name__ == "__main__":
    sys.exit(main())
