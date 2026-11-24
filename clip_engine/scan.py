"""Cam scan of whole clips (cam find only, no render): annotated frame with the rect drawn, a 3-frame contact sheet, one json line.

scan_frames() works on frames (testable without video); scan_media() samples a media file. Never guesses: a clip whose cam is not
confident still gets its frames, with the best candidate drawn in red and every reason in the json.
"""
import json
import os

import numpy as np

from . import camfind

WIN_START, WIN_MAX_END = 2.0, 29.0
SHEET_W = 640
ANNOT_W = 1280


def window_of(length):
    """(start, dur) of the scan/render window: 2 s to min(29, clip length)."""
    end = min(WIN_MAX_END, float(length))
    return WIN_START, max(0.0, end - WIN_START)


def _candidate_rect(info):
    """Best (rect, confidence, reasons, face_hit_pct) from a refusal's evidence: stable-overlay candidates first, then border lines."""
    best = None
    for name, ev in (("stable_overlay", (info or {}).get("stable_overlay") or {}), ("border_lines", info or {})):
        c = (ev.get("candidates") or [None])[0]
        if c and (best is None or c["confidence"] > best[1]):
            best = (c["rect"], c["confidence"], c["reasons"], round(100 * c["face"]["hit"], 1), name)
    return best


def _draw(img, rect, color, label):
    import cv2
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    cv2.rectangle(img, (x0, y0), (max(x0, x1 - 1), max(y0, y1 - 1)), color, max(3, img.shape[1] // 320))
    cv2.putText(img, label, (x0 + 8, y0 + 4 + int(img.shape[1] / 40)), cv2.FONT_HERSHEY_SIMPLEX, img.shape[1] / 1600.0, color,
                max(2, img.shape[1] // 640))


def _resize(img, w):
    import cv2
    return cv2.resize(img, (w, int(round(img.shape[0] * w / img.shape[1]))), interpolation=cv2.INTER_AREA)


def scan_frames(color_frames, face_fn=None, name=""):
    """-> (result dict, annotated BGR frame (full size), contact sheet BGR (3 frames stacked, SHEET_W wide)).
    result: ok, method, rect_px, rect_frac, confidence, face_hit_pct, reasons, best_candidate."""
    import cv2
    gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in color_frames]
    h, w = gray[0].shape
    res = {"clip": name, "ok": False, "frames": len(gray), "size": [w, h]}
    try:
        r = camfind.detect_auto(gray, face_fn=face_fn)
        rect = r["cam"]["rect_px"]
        faces = r["cam"]["faces_in_rect"]
        res.update(ok=True, method=r["method"], rect_px=rect, rect_frac=r["cam"]["rect_frac"], confidence=r["confidence"],
                   face_hit_pct=r.get("face_hit_pct") or (round(100 * max(f["hit"] for f in faces), 1) if faces else 0.0),
                   reasons=[], zones={k: [round(v) for v in z] for k, z in r["zones"].items()})
        color, label = (0, 220, 0), f"{r['method']} {r['confidence']}"
    except camfind.CamNotConfident as e:
        res.update(error=str(e)[:900])
        c = _candidate_rect(e.info)
        rect = None
        if c:
            rect = c[0]
            res.update(method=c[4], rect_px=[round(v) for v in rect], rect_frac=camfind._frac(rect, w, h), confidence=c[1],
                       face_hit_pct=c[3], reasons=c[2])
        else:
            res.update(reasons=["no face track and no candidate"])
        color, label = (0, 0, 255), f"NOT CONFIDENT {res.get('confidence', '')}"
    mid = color_frames[len(color_frames) // 2].copy()
    pick = [color_frames[len(color_frames) // 4], color_frames[len(color_frames) // 2], color_frames[3 * len(color_frames) // 4]]
    ann = []
    for f in [mid] + pick:
        f = f.copy()
        if rect is not None:
            _draw(f, rect, color, label)
        ann.append(f)
    sheet = np.vstack([_resize(f, SHEET_W) for f in ann[1:]])
    return res, ann[0], sheet


def scan_media(media, w_src, h_src, length, name="", face_fn=None, n=camfind.N_SAMPLES):
    start, dur = window_of(length)
    if dur < 4:
        raise ValueError(f"clip is {length:.1f} s: window {start}-{min(WIN_MAX_END, length)} is too short")
    times = camfind.sample_times(start, dur, n)
    frames = camfind.grab(media, times, w_src, h_src, color=True)
    res, ann, sheet = scan_frames(frames, face_fn=face_fn, name=name)
    res["window"] = [start, round(start + dur, 2)]
    return res, ann, sheet


def save(res, ann, sheet, out_root, clip_id):
    import cv2
    for sub, img, w in (("scan_annotated", ann, ANNOT_W), ("scan", sheet, SHEET_W)):
        d = os.path.join(out_root, sub)
        os.makedirs(d, exist_ok=True)
        cv2.imwrite(os.path.join(d, clip_id + ".jpg"), _resize(img, w) if img.shape[1] != w else img, [cv2.IMWRITE_JPEG_QUALITY, 85])


def line(res):
    """One line: clip, method, rect (frac), confidence, face hits %, pass/fail."""
    if "rect_frac" not in res:
        return f"{res['clip']}: NO CANDIDATE ({res.get('error', '')[:120]})"
    f = res["rect_frac"]
    return (f"{res['clip']}: {'CONFIDENT' if res['ok'] else 'not confident'} [{res.get('method')}] rect_frac "
            f"{f[0]:.3f},{f[1]:.3f},{f[2]:.3f},{f[3]:.3f} conf {res['confidence']} face_hits {res['face_hit_pct']}%")


def write_report(results, path):
    with open(path, "w") as f:
        json.dump(results, f, indent=1)
