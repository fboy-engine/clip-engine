"""Two-block 9:16 layout: facecam block on top (~28% of the height), gameplay panel below.

Pure geometry (numpy only). The cam block is cropped around the streamer's face and shoulders;
the panel is a crop of the whole game frame that follows the subject.
"""
import numpy as np

from .config import OUT_W, OUT_H

CAM_H = 540                       # 28% of 1920
PANEL_H = OUT_H - CAM_H           # 1380: always taller than the cam block
CAM_ASPECT = OUT_W / CAM_H        # 2.0
PANEL_ASPECT = OUT_W / PANEL_H
MAX_CAM_UPSCALE = 3.0             # never blow a small cam up more than this
FACE_BOX_FACTOR = 4.2             # crop width = face width * this (face + shoulders)
FACE_ROW = 0.40                   # face centre sits at this fraction of the cam block height
NOMINAL_CAM = {"bottom-left": (0.0, 0.62, 0.26, 1.0), "bottom-right": (0.74, 0.62, 1.0, 1.0),
               "top-left": (0.0, 0.0, 0.26, 0.38), "top-right": (0.74, 0.0, 1.0, 0.38),
               "mid-left": (0.0, 0.30, 0.26, 0.70), "mid-right": (0.74, 0.30, 1.0, 0.70),
               "top-mid": (0.37, 0.0, 0.63, 0.38), "bottom-mid": (0.37, 0.62, 0.63, 1.0)}


def panel_wh(src_w, src_h):
    """Source-pixel size of the gameplay crop at zoom 0 (845x1080 on a 1080p source)."""
    h = float(src_h)
    w = h * PANEL_ASPECT
    if w > src_w:
        w, h = float(src_w), src_w / PANEL_ASPECT
    return w, h


def est_cam_rect(corner, obs, src_w, src_h):
    """Cam rectangle (fractions) = nominal corner box united with the padded detected face."""
    x0, y0, x1, y1 = NOMINAL_CAM[corner]
    if obs:
        cx = float(np.median([o[1] for o in obs]))
        cy = float(np.median([o[2] for o in obs]))
        fw = float(np.median([o[3] for o in obs]))
        x0 = min(x0, max(0.0, (cx - 1.8 * fw) / src_w))
        x1 = max(x1, min(1.0, (cx + 1.8 * fw) / src_w))
        y0 = min(y0, max(0.0, (cy - 1.8 * fw) / src_h))
        y1 = max(y1, min(1.0, (cy + 2.6 * fw) / src_h))
    return (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4))


def clean_obs(obs, k=2.5):
    """Drop face detections far from the median position (false hits on game characters)."""
    if len(obs) < 3:
        return list(obs)
    cx = np.array([o[1] for o in obs])
    cy = np.array([o[2] for o in obs])
    fw = np.median([o[3] for o in obs])
    dx, dy = np.abs(cx - np.median(cx)), np.abs(cy - np.median(cy))
    keep = (dx <= max(k * np.median(dx), 0.6 * fw)) & (dy <= max(k * np.median(dy), 0.6 * fw))
    return [o for o, ok in zip(obs, keep) if ok]


def _smooth(x, sigma):
    x = np.asarray(x, dtype=np.float64)
    if sigma < 0.5 or len(x) < 3:
        return x
    r = int(3 * sigma)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    return np.convolve(np.pad(x, r, mode="edge"), k / k.sum(), mode="valid")


def face_path(obs, n, fps, sigma_sec=1.0):
    """(cx, cy, fw): per-output-frame face centre (smoothed) and ONE constant face width,
    so the cam block never pumps in and out."""
    obs = clean_obs(obs)
    ts = np.arange(n) / fps
    t = [o[0] for o in obs]
    cx = np.interp(ts, t, [o[1] for o in obs])
    cy = np.interp(ts, t, [o[2] for o in obs])
    fw = float(np.median([o[3] for o in obs]))
    return _smooth(cx, sigma_sec * fps), _smooth(cy, sigma_sec * fps), fw


def cam_boxes(path, region, full=False):
    """Per-frame source boxes (left, top, w, h), w/h == 2, inside `region` (px x0,y0,x1,y1).
    Width is face width * 4.2 (face + shoulders), at least out_w/3 so the upscale stays moderate.
    full=True (a generic-detected cam): the biggest 2:1 window the region holds, so the whole cam is shown, never stretched."""
    cx, cy, fw = path
    rx0, ry0, rx1, ry1 = region
    if full:
        w = min(rx1 - rx0, (ry1 - ry0) * CAM_ASPECT)
    else:
        w = max(fw * FACE_BOX_FACTOR, OUT_W / MAX_CAM_UPSCALE)
        w = min(w, rx1 - rx0, (ry1 - ry0) * CAM_ASPECT)
    h = w / CAM_ASPECT
    out = []
    for x, y in zip(cx, cy):
        left = min(max(x - w / 2, rx0), rx1 - w)
        top = min(max(y - FACE_ROW * h, ry0), ry1 - h)
        out.append((float(left), float(top), float(w), float(h)))
    return out


def region_px(rect, src_w, src_h):
    return (int(rect[0] * src_w), int(rect[1] * src_h), int(np.ceil(rect[2] * src_w)), int(np.ceil(rect[3] * src_h)))


def upscale(boxes):
    return OUT_W / boxes[0][2] if boxes else 1.0


def layout_report(src_w, src_h, cam_rect, cam_info, bxs, exclusion_info=None):
    """layout.json: the permanent 1080x1920 layout as actually rendered (cam rect, top block, crop rect,
    scale factors, exclusion check results)."""
    lefts = [b[0] for b in bxs]
    b0 = bxs[0] if bxs else (0.0, 0.0, 0.0, 0.0)
    return {
        "output": {"w": OUT_W, "h": OUT_H},
        "source": {"w": src_w, "h": src_h},
        "top_block": {"w": OUT_W, "h": CAM_H, "pct_of_height": round(100 * CAM_H / OUT_H, 1)},
        "gameplay_panel": {"y0": CAM_H, "w": OUT_W, "h": PANEL_H},
        "cam_rect_frac": list(cam_rect) if cam_rect else None,
        "cam_rect_px": [round(v) for v in (cam_rect[0] * src_w, cam_rect[1] * src_h, cam_rect[2] * src_w,
                                           cam_rect[3] * src_h)] if cam_rect else None,
        "cam_box_px": (cam_info or {}).get("box_px"),
        "cam_upscale": (cam_info or {}).get("upscale"),
        "crop_rect_px": {"first": [round(v, 1) for v in b0],
                         "left_min": round(min(lefts), 1) if lefts else None,
                         "left_max": round(max(lefts), 1) if lefts else None,
                         "w": round(b0[2], 1), "h": round(b0[3], 1)},
        "panel_scale": round(OUT_W / b0[2], 3) if b0[2] else None,
        "exclusion": exclusion_info or {"checked": False},
    }
