"""The gameplay panel, measured exactly the way the post-render grade measures it.

grade_clip.subject_output re-detects people in the RENDERED panel (2 fps, the panel scaled to 540 px wide, no masks) and
classifies every sample ok / cut / unseen. The render-time tracker looks at a different scale and with masks, so the two
used to disagree (render said 91%, the grade measured 77%). This module runs the SAME detector on the SAME kind of
frames before the expensive cam work, so a clip the grade would fail is refused early with the grade's own numbers.
The detection and the ok/cut rule live here and in subject.classify_output only; grade_clip calls detect_panel too.
"""
from . import subject

GRADE_FPS = 2              # the grade samples the rendered panel at this rate
GRADE_W = 540              # ... scaled to this width
PANEL_H = 1380             # rendered panel height at 1080 wide (layout.PANEL_H)
PASS_PCT = 95.0            # same bar as grade_checks.check_subject
MIN_DETECTED_PCT = 25.0    # below this the grade cannot verify (WARN, not FAIL); neither do we refuse


def detect_panel(im, w_out=GRADE_W):
    """People/vehicles found in a panel image: pixel boxes (x0, y0, x1, y1, score). Same call the grade makes."""
    from . import vision
    hh = im.shape[0]
    return [(b[0] * w_out, b[1] * hh, b[2] * w_out, b[3] * hh, b[4]) for b in vision.detect_people(im)]


def panel_frame(frame, box, out_w=GRADE_W, panel_h=PANEL_H, out_full_w=1080):
    """The source frame cropped to the panel box (left, top, w, h in source px), scaled to what the grade sees."""
    import cv2
    left, top, w, h = box
    x0, y0 = max(int(round(left)), 0), max(int(round(top)), 0)
    x1, y1 = int(round(left + w)), int(round(top + h))
    crop = frame[y0:y1, x0:x1]
    hh = int(round(panel_h * out_w / out_full_w / 2) * 2)
    return cv2.resize(crop, (out_w, hh), interpolation=cv2.INTER_AREA)


def measure(frames, boxes, times, detect=detect_panel, prep=panel_frame, out_w=GRADE_W):
    """frames: source frames at the grade's sample times; boxes: the crop box shown at each of them.
    Returns the grade's summary (inside_pct, detected_pct, cut_times, unseen_times, samples) plus labels."""
    dets = [detect(prep(fr, bx), out_w) for fr, bx in zip(frames, boxes)]
    labels = subject.classify_output(dets, out_w)
    out = subject.summarize_output(labels, list(times)[:len(labels)])
    out["labels"] = labels
    return out


def verdict(m):
    """(ok, text). The grade's rule: not enough detections = cannot verify (not a refusal); else inside >= PASS_PCT."""
    inside, det, n = m.get("inside_pct"), m.get("detected_pct", 0.0), m.get("samples")
    if inside is None or det < MIN_DETECTED_PCT:
        return True, f"cannot verify: a person is detected in only {det}% of {n} samples"
    text = f"{inside}% inside on {det}% detected frames of {n}"
    if inside < PASS_PCT:
        cuts = ", ".join(f"{t:g}s" for t in m.get("cut_times", [])[:12])
        return False, text + f" (needs >= {PASS_PCT:g}%); cut at {cuts}"
    return True, text


def run(media, start, dur, src_w, src_h, bxs, fps, log=print):
    """Decode the clip's window at the grade's rate, show each sample through the crop box of that moment, measure.
    bxs: per output frame (left, top, w, h) in source px; fps: the output frame rate."""
    from . import vision
    frames, boxes, times = [], [], []
    for t, fr in vision.frames_iter(media, start, dur, GRADE_FPS, src_w, src_h):
        frames.append(fr)
        boxes.append(bxs[min(int(round(t * fps)), len(bxs) - 1)])
        times.append(t)
    m = measure(frames, boxes, times)
    ok, text = verdict(m)
    log(f"panel check (the grade's own measurement, before the cam work): {text}")
    return {**m, "ok": ok, "detail": text}
