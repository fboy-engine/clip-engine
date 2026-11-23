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

from . import cam_enhance, cam_sr, camresolve, download, exclusion, layout, quality, srcinfo, window

SWS = "lanczos+accurate_rnd+full_chroma_int"
N_FRAMES = 8
FRAME_STEP_SEC = 0.5        # about one sample frame per 0.5 s of the window ...
MIN_FRAMES, MAX_FRAMES = 8, 24   # ... but never fewer than 8 nor more than 24
SHORT_SOURCE_SEC = 40       # windows shorter than this need fewer real crops
MIN_CROPS_SHORT, MIN_CROPS_LONG = 6, 8


class NotEnoughCrops(RuntimeError):
    """The source gave too few real crops to benchmark. Not a render blocker: the bench is skipped with a warning."""


def sample_count(dur):
    """Sample frames for a window of dur seconds: about 1 per 0.5 s, at least 8, at most 24."""
    return int(max(MIN_FRAMES, min(MAX_FRAMES, round(float(dur) / FRAME_STEP_SEC))))


def min_crops(dur):
    """Real crops that must be cut for the benchmark to count: 6 for windows under 40 s, 8 for longer ones."""
    return MIN_CROPS_SHORT if float(dur) < SHORT_SOURCE_SEC else MIN_CROPS_LONG


def check_crops(n_real, have_roi, dur):
    need = min_crops(dur)
    if n_real < need or not have_roi:
        raise NotEnoughCrops(f"only {n_real} real crops could be cut from the source (need {need} for a {float(dur):.0f} s window"
                             + ("" if have_roi else ", and the cam ROI frame could not be cut") + ")")
    return need


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


def pick_boxes(cam, rect, src_w, src_h, fps, dur, n=N_FRAMES, region=None, full=False):
    """Integer crops (x, y, w, h) the render would use, at n evenly spaced moments: w a multiple of 6, 2:1."""
    region = tuple(region) if region else layout.region_px(rect, src_w, src_h)
    count = max(1, int(dur * fps))
    path = layout.face_path(cam["obs"], count, fps)
    boxes = layout.cam_boxes(path, region, full=full)
    out = []
    for k in range(n):
        i = min(count - 1, int((k + 0.5) / n * count))
        l, t, bw, _ = boxes[i]
        w = int(bw) // 6 * 6
        h = w // 2
        x = int(min(max(round(l), region[0]), region[2] - w))
        y = int(min(max(round(t), region[1]), region[3] - h))
        x, y = x - x % 2, y - y % 2                     # even crop origin (yuv420p), still inside the region (its edges are even)
        out.append({"t_off": i / fps, "x": x, "y": y, "w": w, "h": h})
    return out, region


def roi_of(b, region, m=cam_sr.ROI_MARGIN):
    x0, y0 = max(region[0], b["x"] - m), max(region[1], b["y"] - m)
    x1, y1 = min(region[2], b["x"] + b["w"] + m), min(region[3], b["y"] + b["h"] + m)
    x0, y0, x1, y1 = x0 - x0 % 2, y0 - y0 % 2, x1 - x1 % 2, y1 - y1 % 2        # even: ffmpeg crop on yuv420p rounds odd values down
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


def md_table(res, sel, deb, sinfo, fmt_note, b3=None):
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
    if b3:
        L += ["", "## B3 chain (what the render uses)", "",
              f"Upscaler: **{b3['method']}** at {b3['cam_fps']} fps cam ({b3['reason']}). Deblock: {b3['deblock']}. "
              f"SR share: {b3['blend'] if b3['blend'] is not None else 'NONE PASSES'} (start {b3['blend_start']}). "
              f"Gate: **{'PASS' if b3['ok'] else 'FAIL'}**" + ("" if b3["ok"] else " - " + "; ".join(b3["reasons"])), "",
              "| SR share | fidelity | blockiness | edge |", "|---|---|---|---|"]
        for w, r in b3["blend_rows"].items():
            L.append(f"| {w} | {r['fidelity']} | {r['blockiness']} | {r['edge']} |")
        o = b3["old_lanczos"]
        L.append(f"| plain lanczos (old chain) | {o['fidelity']} | {o['blockiness']} | {o['edge']} |")
        L += ["", f"Gate: fidelity >= {b3['fidelity_min']} (block shrunk back vs the untouched real crop), blockiness lower than "
                  "plain lanczos, edge strength higher than plain lanczos."]
    return "\n".join(L) + "\n"


def run(media, start, end, out_dir, vod_id="", url="", log=print, cache_dir=None, cookies=None, proxy=None,
        candidates=cam_sr.CANDIDATES, n_frames=None, cam_rect=None):
    import cv2
    from . import render_clip
    os.makedirs(out_dir, exist_ok=True)
    pr = download.probe_media(media)
    src_w, src_h, fps = pr["width"], pr["height"], pr["fps"]
    sinfo = srcinfo.media_info(media)
    log("source: " + srcinfo.info_line(sinfo))
    zones = exclusion.zones_for(exclusion.vod_id_from_url(vod_id), src_w, src_h)
    start, end = window.check_window(start, end, pr["duration"])      # empty/zero window: clear error, never 0 samples
    dur = end - start
    # a known VOD keeps its zone seed; otherwise the generic detector decides (same decision as the render: camresolve)
    gen = camresolve.resolve(media, start, dur, src_w, src_h, out_dir, zones, cam_rect, None, log)
    rect = gen["cam_rect"] or exclusion.cam_seed_frac(zones, src_w, src_h)
    if not rect:
        raise ValueError("cam bench: no cam rectangle (not a known VOD and the generic detector gave none)")
    generic = gen["generic"]
    cam = gen["cam"] if generic else render_clip.seeded_cam(media, start, dur, rect, src_w, src_h, log)
    if cam is None:
        raise ValueError("no facecam verified in the cam zone: no real crops to benchmark")
    n_frames = n_frames or sample_count(dur)
    boxes, region = pick_boxes(cam, rect, src_w, src_h, fps, dur, n_frames, region=gen["region"], full=generic)
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
    check_crops(len(raw), roi is not None, dur)         # too few real crops: NotEnoughCrops (the CLI skips the bench, the render goes on)

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

    # ---- B3: the chain the render uses (user decision: Real-ESRGAN), SR share chosen by measurement ----------
    b3sel = cam_sr.choose_b3(results)
    up3 = ups.get(b3sel["method"]) or ups["lanczos"]
    log(f"B3 upscaler: {b3sel['method']} at {b3sel['cam_fps']} fps ({b3sel['reason']}); rejected: {json.dumps(b3sel['rejected'])}")
    idx = sorted({0, len(raw) // 3, (2 * len(raw)) // 3, len(raw) - 1})
    per_w = {w: [] for w in cam_sr.BLEND_STEPS}
    per_old, shown = [], []
    for j in idx:
        r = raw[j]
        b = r["b"]
        rx, ry, rw, rh = roi_of(b, region)
        raw_roi = grab(media, r["t"], crop_vf("", rx, ry, rw, rh), rw, rh)
        clean_roi = grab(media, r["t"], crop_vf(deb_vf, rx, ry, rw, rh, cam_sr.DENOISE_VF), rw, rh)
        if raw_roi is None or clean_roi is None:
            continue
        roi = (rx, ry, rw, rh)
        box = (float(b["x"]), float(b["y"]), float(b["w"]), float(b["h"]))
        ref = cam_enhance.real_small(raw_roi, box, (rx, ry))
        old = cam_enhance.old_block(raw_roi, box, (rx, ry))
        per_old.append(cam_enhance.block_metrics(old, ref, box))
        sr4 = up3.run4(clean_roi)
        lan4 = cv2.resize(clean_roi, None, fx=cam_sr.SR_FACTOR, fy=cam_sr.SR_FACTOR, interpolation=cv2.INTER_LANCZOS4)
        for w in cam_sr.BLEND_STEPS:
            mixed = sr4 if w >= 0.999 else cv2.addWeighted(sr4, w, lan4, 1.0 - w, 0)
            per_w[w].append(cam_enhance.block_metrics(cam_enhance.to_block(mixed, box, roi, seed=int(r["t"] * 100)), ref, box))
        shown.append((old, sr4, lan4, box, roi, r["t"]))
    if not per_old:
        raise RuntimeError("B3 measurement: no ROI crops could be cut")
    rows_w = {w: cam_enhance.mean_rows(v) for w, v in per_w.items() if v}
    base = cam_enhance.mean_rows(per_old)
    if b3sel["method"] == "lanczos":
        pick = {"blend": None, "ok": False, "reasons": ["no neural upscaler is available inside the time budget (plain lanczos is not shipped)"]}
    else:
        pick = cam_sr.choose_blend(rows_w, base, prefer=cam_sr.BLEND_START)
    log(f"B3 blend: {pick} | plain lanczos {base} | rows {json.dumps({str(k): v for k, v in rows_w.items()})}")
    b3 = {"method": b3sel["method"], "cam_fps": b3sel["cam_fps"], "reason": b3sel["reason"], "rejected": b3sel["rejected"],
          "deblock": chosen, "deblock_vf": deb_vf, "denoise_vf": cam_sr.DENOISE_VF, "blend": pick["blend"],
          "blend_start": cam_sr.BLEND_START, "ok": bool(pick["ok"]), "reasons": pick["reasons"],
          "sec_per_frame_bench_roi": results.get(b3sel["method"], {}).get("sec_per_frame"),
          "old_lanczos": base, "blend_rows": {str(k): v for k, v in rows_w.items()}, "frames_measured": len(per_old),
          "fidelity_min": cam_sr.FIDELITY_MIN, "sr_factor": cam_sr.SR_FACTOR,
          "passing_shares": pick.get("passing", []),
          "gate": cam_sr.gate_numbers(rows_w[pick["blend"]], base) if pick["blend"] is not None else None}

    # cam_compare.png: OLD chain (lanczos) over NEW chain on the SAME frame, real 1080x540 size, two moments
    show_w = pick["blend"] if pick["blend"] else cam_sr.BLEND_START
    rows_png = []
    for old, sr4, lan4, box, roi, t in (shown[0], shown[len(shown) // 2]):
        mixed = sr4 if show_w >= 0.999 else cv2.addWeighted(sr4, show_w, lan4, 1.0 - show_w, 0)
        new = cam_enhance.to_block(mixed, box, roi, seed=int(t * 100))
        rows_png.append(label(old, "OLD: bilateral + lanczos + sharpen"))
        rows_png.append(label(new, f"NEW: deblock {chosen} + hqdn3d + {b3sel['method']} {int(show_w * 100)}% + sharpen + grain"))
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
              "selection": sel, "chain": cam_sr.chain_description(chosen, sel["method"], sel["cam_fps"]), "b3": b3}
    with open(os.path.join(out_dir, "cam_bench.json"), "w") as f:
        json.dump(report, f, indent=1)
    md = md_table(results, sel, report["deblock"], sinfo, fmt_note, b3)
    with open(os.path.join(out_dir, "cam_bench.md"), "w") as f:
        f.write(md)
    return report, md
