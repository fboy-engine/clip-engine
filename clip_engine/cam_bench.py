"""Cam upscaler benchmark on the REAL cam crops of the cached source.

Per sample frame: the (about) 360x180 crop the render would use is cut from the source, shrunk 3x, upscaled 3x
by every candidate and scored against the original crop (PSNR, SSIM, blockiness). Time per frame is measured
first, on a production-size input. The deblock filters are scored on the same crops. Writes out/cam_bench.json,
cam_bench.md and cam_compare.png (plain lanczos vs the winning chain, side by side).
"""
import json
import os
import subprocess

import numpy as np

from . import cam_sr, download, exclusion, layout, quality, srcinfo, window

SWS = "lanczos+accurate_rnd+full_chroma_int"
N_FRAMES = 8


def grab(media, t, vf, w, h):
    """One decoded frame at t through the ffmpeg filter chain vf, as BGR uint8 HxWx3 (None if it failed)."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", media, "-frames:v", "1", "-vf", vf,
           "-sws_flags", SWS, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if len(p.stdout) != w * h * 3:
        return None
    return np.frombuffer(p.stdout, np.uint8).reshape(h, w, 3).copy()


def crop_vf(prefix, x, y, w, h, suffix=""):
    return ",".join(s for s in (prefix, f"crop={w}:{h}:{x}:{y}", suffix) if s)


def pick_boxes(cam, rect, src_w, src_h, fps, dur, n=N_FRAMES):
    """Integer crops (x, y, w, h) the render would use, at n evenly spaced moments: w a multiple of 6, 2:1."""
    region = layout.region_px(rect, src_w, src_h)
    count = max(1, int(dur * fps))
    path = layout.face_path(cam["obs"], count, fps)
    boxes = layout.cam_boxes(path, region)
    out = []
    for k in range(n):
        i = min(count - 1, int((k + 0.5) / n * count))
        l, t, bw, _ = boxes[i]
        w = int(bw) // 6 * 6
        h = w // 2
        x = int(min(max(round(l), region[0]), region[2] - w))
        y = int(min(max(round(t), region[1]), region[3] - h))
        out.append({"t_off": i / fps, "x": x, "y": y, "w": w, "h": h})
    return out, region


def roi_of(b, region, m=cam_sr.ROI_MARGIN):
    x0, y0 = max(region[0], b["x"] - m), max(region[1], b["y"] - m)
    x1, y1 = min(region[2], b["x"] + b["w"] + m), min(region[3], b["y"] + b["h"] + m)
    return x0, y0, x1 - x0, y1 - y0


def label(img, text):
    import cv2
    out = img.copy()
    cv2.rectangle(out, (0, out.shape[0] - 30), (out.shape[1], out.shape[0]), (0, 0, 0), -1)
    cv2.putText(out, text, (12, out.shape[0] - 9), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def fit_block(img):
    import cv2
    if img.shape[:2] == (layout.CAM_H, 1080):
        return img
    return cv2.resize(img, (1080, layout.CAM_H), interpolation=cv2.INTER_AREA if img.shape[1] > 1080 else cv2.INTER_LANCZOS4)


def md_table(res, sel, deb, sinfo, fmt_note):
    L = ["# Cam upscaler benchmark (real cam crops of the cached source)", "",
         f"Source: {srcinfo.info_line(sinfo)}", fmt_note, "",
         f"Winner: **{sel['method']}** at {sel['cam_fps']} fps cam ({sel['reason']})", "",
         "| method | s/frame | PSNR dB | SSIM | blockiness | edge | prod blockiness | prod fidelity | verdict |",
         "|---|---|---|---|---|---|---|---|---|"]
    for name, r in res.items():
        if r.get("error"):
            L.append(f"| {name} | - | - | - | - | - | - | - | {r['error']} |")
            continue
        p = r.get("prod", {})
        verdict = "WINNER" if name == sel["method"] else sel["rejected"].get(name, "ok" if name == "lanczos" else "")
        L.append(f"| {name} | {r['sec_per_frame']} | {r['psnr']} | {r['ssim']} | {r['blockiness']} | {r['edge']} | "
                 f"{p.get('blockiness', '-')} | {p.get('fidelity_ssim', '-')} | {verdict} |")
    L += ["", "Benchmark = real crop shrunk 3x, upscaled 3x, compared with the original crop (blockiness: lower is "
          "better, ~1.0 = no block grid). prod = the cleaned real crop upscaled to 1080x540; fidelity = SSIM of that "
          "block shrunk back to the crop size against the untouched crop (the face must not change).", "",
          "## Deblock filters (full frame, before the crop)", "", "| variant | blockiness | SSIM vs untouched | chosen |",
          "|---|---|---|---|"]
    for name, r in deb["rows"].items():
        L.append(f"| {name} | {r.get('blockiness', '-')} | {r.get('ssim', '-')} | {'yes' if name == deb['chosen'] else ''} |"
                 if not r.get("error") else f"| {name} | - | - | {r['error']} |")
    return "\n".join(L) + "\n"


def run(media, start, end, out_dir, vod_id="", url="", log=print, cache_dir=None, cookies=None, proxy=None,
        candidates=cam_sr.CANDIDATES, n_frames=N_FRAMES):
    import cv2
    from . import render_clip
    os.makedirs(out_dir, exist_ok=True)
    pr = download.probe_media(media)
    src_w, src_h, fps = pr["width"], pr["height"], pr["fps"]
    sinfo = srcinfo.media_info(media)
    log("source: " + srcinfo.info_line(sinfo))
    zones = exclusion.zones_for(exclusion.vod_id_from_url(vod_id), src_w, src_h)
    rect = exclusion.cam_seed_frac(zones, src_w, src_h)
    if not rect:
        raise ValueError("cam bench needs the cam zone of a known VOD (--vod-id)")
    start, end = window.check_window(start, end, pr["duration"])      # empty/zero window: clear error, never 0 samples
    dur = end - start
    cam = render_clip.seeded_cam(media, start, dur, rect, src_w, src_h, log)
    if cam is None:
        raise ValueError("no facecam verified in the cam zone: no real crops to benchmark")
    boxes, region = pick_boxes(cam, rect, src_w, src_h, fps, dur, n_frames)
    log(f"cam region {region}, crop {boxes[0]['w']}x{boxes[0]['h']} px (x{cam_sr.SCALE} = "
        f"{boxes[0]['w'] * cam_sr.SCALE}x{boxes[0]['h'] * cam_sr.SCALE}), {len(boxes)} sample frames")

    raw, roi = [], None
    for b in boxes:
        t = start + b["t_off"]
        img = grab(media, t, crop_vf("", b["x"], b["y"], b["w"], b["h"]), b["w"], b["h"])
        if img is None:
            continue
        raw.append({"img": img, "ox": (-b["x"]) % 8, "oy": (-b["y"]) % 8, "b": b, "t": t})
        if roi is None:
            rx, ry, rw, rh = roi_of(b, region)
            roi = grab(media, t, crop_vf("", rx, ry, rw, rh), rw, rh)
    if len(raw) < 3 or roi is None:
        raise RuntimeError(f"only {len(raw)} real crops could be cut from the source")

    # deblock filters on the same crops (filter runs on the FULL frame so its block grid stays aligned)
    deb_rows = {}
    for name, vf in cam_sr.DEBLOCK_VARIANTS.items():
        rows, err = [], None
        for r in raw:
            b = r["b"]
            img = r["img"] if not vf else grab(media, r["t"], crop_vf(vf, b["x"], b["y"], b["w"], b["h"]), b["w"], b["h"])
            if img is None:
                err = "ffmpeg filter unavailable or failed"
                break
            rows.append((quality.blockiness(img, 8, r["ox"], r["oy"]), quality.ssim(r["img"], img)))
        deb_rows[name] = {"error": err} if err else {"blockiness": round(float(np.mean([x[0] for x in rows])), 4),
                                                     "ssim": round(float(np.mean([x[1] for x in rows])), 4)}
    chosen = cam_sr.choose_deblock(deb_rows)
    log(f"deblock: {chosen} ({json.dumps(deb_rows)})")
    deb_vf = cam_sr.DEBLOCK_VARIANTS[chosen]
    for r in raw:                                       # cleaned crops (deblock + light denoise) for the production test
        b = r["b"]
        r["clean"] = grab(media, r["t"], crop_vf(deb_vf, b["x"], b["y"], b["w"], b["h"], cam_sr.DENOISE_VF), b["w"], b["h"])
        if r["clean"] is None:
            r["clean"] = r["img"]

    crops = [{"img": r["img"], "ox": r["ox"], "oy": r["oy"]} for r in raw]
    results, ups = {}, {}
    for name in candidates:
        try:
            up = cam_sr.make(name).load(cache_dir)
            res = cam_sr.bench_candidate(up, crops, roi, log)
        except Exception as e:
            results[name] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
            log(f"{name}: unavailable ({results[name]['error']})")
            continue
        ups[name] = up
        k = max(1, min(4, len(raw)))
        prod = []
        for r in raw[:k]:
            clean = r["img"] if name == "lanczos" else r["clean"]       # the lanczos row is the PLAIN old path: no cleaning
            out = up.run(clean)
            back = cv2.resize(out, (r["img"].shape[1], r["img"].shape[0]), interpolation=cv2.INTER_AREA)
            prod.append((quality.blockiness(out, 8 * cam_sr.SCALE, r["ox"] * cam_sr.SCALE, r["oy"] * cam_sr.SCALE),
                         quality.ssim(r["img"], back)))
        res["prod"] = {"blockiness": round(float(np.mean([p[0] for p in prod])), 4),
                       "fidelity_ssim": round(float(np.mean([p[1] for p in prod])), 4)}
        results[name] = res
        log(f"{name}: psnr {res['psnr']} ssim {res['ssim']} blockiness {res['blockiness']} edge {res['edge']} "
            f"| prod blockiness {res['prod']['blockiness']} fidelity {res['prod']['fidelity_ssim']}")
    sel = cam_sr.choose(results)
    log(f"winner: {sel['method']} at {sel['cam_fps']} fps ({sel['reason']}); rejected: {json.dumps(sel['rejected'])}")

    # cam_compare.png: plain lanczos (raw crop) | chosen chain, two moments
    win = ups.get(sel["method"]) or ups["lanczos"]
    rows_png = []
    for r in (raw[0], raw[len(raw) // 2]):
        plain = fit_block(ups["lanczos"].run(r["img"]))
        enh = fit_block(cam_sr.finish(win.run(r["clean"]), seed=int(r["t"] * 100)))
        rows_png.append(np.hstack([label(plain, "plain lanczos x3"),
                                   label(enh, f"deblock {chosen} + hqdn3d + {sel['method']} + sharpen + grain")]))
    png = os.path.join(out_dir, "cam_compare.png")
    cv2.imwrite(png, np.vstack(rows_png))

    formats, ferr = (None, "no url given")
    if url:
        formats, ferr = srcinfo.format_list(url, cookies, proxy)
    better = srcinfo.better_formats(sinfo, formats) if formats else []
    if formats is None:
        fmt_note = f"Better source format: could not list the platform formats ({ferr or 'unknown'}); nothing was downloaded."
    elif better:
        fmt_note = ("Better source formats exist (nothing downloaded, your call): " +
                    "; ".join(f"{f['id']} {f['vcodec']} {f['height']}p {f['fps']}fps {f['tbr']} kbps" for f in better))
    else:
        fmt_note = "Better source format: none offered beyond the cached file."
    log(fmt_note)
    report = {"source": sinfo, "better_formats": better, "format_listing_error": ferr if formats is None else "",
              "frames": [{k: r["b"][k] for k in ("t_off", "x", "y", "w", "h")} for r in raw],
              "region_px": list(region), "deblock": {"chosen": chosen, "rows": deb_rows}, "candidates": results,
              "selection": sel, "chain": cam_sr.chain_description(chosen, sel["method"], sel["cam_fps"])}
    with open(os.path.join(out_dir, "cam_bench.json"), "w") as f:
        json.dump(report, f, indent=1)
    md = md_table(results, sel, report["deblock"], sinfo, fmt_note)
    with open(os.path.join(out_dir, "cam_bench.md"), "w") as f:
        f.write(md)
    return report, md
