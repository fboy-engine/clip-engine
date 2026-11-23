"""A3-2: decide the cam rectangle and the overlay zones of a render BEFORE anything is cut.

Order of authority (never weakened): a person's cam_rect, a known VOD's zones or a named corner keep the legacy path; only when
none of them is given (auto, no known zones) the generic detector (camfind) runs. When it is not confident it raises
CamNotConfident with its reasons: no guess, no fallback. out/cam_find.jpg (cam rectangle and overlay zones drawn on a frame) and
out/cam_find.json are written on EVERY run, also when the detector refuses.
"""
import json
import os

import numpy as np

from . import camfind

INSET = 3          # px: a cam rectangle's straight border line is not part of the picture, the cam window stays inside it


def mode_of(cam_corner):
    return (cam_corner or "auto").strip().lower()


def is_auto(zones, cam_rect, cam_corner):
    """True when the generic detector has to decide (nothing given, no known VOD zones, no named corner, not gameplay-only)."""
    return not zones and not cam_rect and mode_of(cam_corner) == "auto"


def inset_region(rect_px, sides, m=INSET):
    """Cam region (int px x0, y0, x1, y1): the detected rectangle minus `m` px on every side that is a drawn border line
    (a side that runs to the frame edge or has no border keeps its full extent)."""
    x0, y0, x1, y1 = [float(v) for v in rect_px]
    sides = sides or {}
    if sides.get("left") == "line":
        x0 += m
    if sides.get("right") == "line":
        x1 -= m
    if sides.get("top") == "line":
        y0 += m
    if sides.get("bottom") == "line":
        y1 -= m
    # EVEN edges, always inside the rectangle: ffmpeg's crop on yuv420p rounds odd x/y/w/h down, so an odd region makes the 1-frame grabs
    # come back short (run 45126781594: the bench could not cut its ROI at x=1307)
    def up(v):
        v = int(np.ceil(v))
        return v + (v % 2)

    def down(v):
        v = int(np.floor(v))
        return v - (v % 2)
    return up(x0), up(y0), down(x1), down(y1)


def _plain(o):
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def write_json(out_dir, payload):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "cam_find.json"), "w") as f:
        json.dump(payload, f, indent=1, default=_plain)


def draw_given(media, t, src_w, src_h, path, zones, evidence=None):
    """Annotated source frame for a layout that was given or refused (the detector's own picture is draw_debug)."""
    col = camfind.grab(media, [t], src_w, src_h, color=True)
    if not col:
        return None
    camfind.draw_debug(col[0], {"zones": zones or {}, "cam": {"confidence": "given"}, "evidence": evidence or {}}, path)
    return path


def _try_draw(*a, **k):
    try:
        return draw_given(*a, **k)
    except Exception:       # a debug picture must never be the reason a render fails or hides the real error
        return None


def face_cam(res, dur):
    """The cam dict the render uses (same keys as render_clip.seeded_cam) from a confident detection: ONE stable face track,
    held constant (the cam is a fixed overlay, and a second person in the cam must not make the anchor flip)."""
    c = res["cam"]
    f = c["face"]
    x0, y0, x1, y1 = c["rect_px"]
    corner = ("top" if (y0 + y1) / 2 < res["height"] / 2 else "bottom") + "-" + ("left" if (x0 + x1) / 2 < res["width"] / 2 else "right")
    obs = [(float(t), float(f["cx"]), float(f["cy"]), float(f["w"])) for t in (0.0, dur / 2.0, dur)]
    return {"corner": corner, "obs": obs, "hit_pct": round(100 * f["hit"], 1), "rect": tuple(c["rect_frac"]),
            "how": "generic detect: stable face track + persistent cam borders (camfind)",
            "verify": {"face": f, "confidence": c["confidence"], "sides": c["sides"]}}


def resolve(media, start, dur, src_w, src_h, out_dir, zones, cam_rect, cam_corner, log=print, face_fn=None):
    """-> {generic, cam_rect (fractions or None), zones (px boxes), cam (cam dict or None), region (int px), rect_px, detect}.
    Raises camfind.CamNotConfident (reasons in the message, evidence in out/cam_find.json) when auto-detection cannot decide."""
    jpg = os.path.join(out_dir, "cam_find.jpg")
    mid = start + dur / 2.0
    os.makedirs(out_dir, exist_ok=True)
    if not is_auto(zones, cam_rect, cam_corner):
        shown = {k: tuple(v) for k, v in (zones or {}).items()}
        if cam_rect and "cam" not in shown:
            shown["cam"] = (cam_rect[0] * src_w, cam_rect[1] * src_h, cam_rect[2] * src_w, cam_rect[3] * src_h)
        why = ("known VOD zones" if zones else "given cam_rect" if cam_rect else
               "gameplay only (cam_corner none)" if mode_of(cam_corner) == "none" else f"named corner {mode_of(cam_corner)}")
        _try_draw(media, mid, src_w, src_h, jpg, shown)
        write_json(out_dir, {"source": why, "generic": False, "cam_rect_frac": list(cam_rect) if cam_rect else None,
                             "zones": {k: [round(float(v), 1) for v in z] for k, z in shown.items()}})
        log(f"cam find: not run ({why}); cam_find.jpg shows the given zones")
        return {"generic": False, "cam_rect": cam_rect, "zones": zones, "cam": None, "region": None, "rect_px": None, "detect": None}
    try:
        res = camfind.detect_media(media, start, dur, src_w, src_h, face_fn=face_fn, debug_jpg=jpg)
    except camfind.CamNotConfident as e:
        _try_draw(media, mid, src_w, src_h, jpg, {}, (e.info or {}).get("evidence") or e.info)
        write_json(out_dir, {"source": "generic detector", "ok": False, "error": str(e), "info": e.info})
        log(f"cam find: NOT confident: {e}")
        raise
    write_json(out_dir, {"source": "generic detector", "ok": True, "result": res})
    c = res["cam"]
    region = inset_region(c["rect_px"], c["sides"])
    log(f"cam find: cam rect {c['rect_px']} confidence {c['confidence']} sides {c['sides']}; region used {region}; "
        f"chat {res['chat']['rect'] if res.get('chat') else None}; zones {sorted(res['zones'])}")
    return {"generic": True, "cam_rect": list(c["rect_frac"]), "zones": {k: tuple(v) for k, v in res["zones"].items()},
            "cam": face_cam(res, dur), "region": region, "rect_px": tuple(float(v) for v in c["rect_px"]), "detect": res}
