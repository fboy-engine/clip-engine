"""OpenCV detectors (lazy import): the streamer's face for the cam block, a person for the subject
track. Everything that can be pure (corner choice, region maths) is pure and unit-tested."""
import subprocess

import numpy as np

from .layout import region_px, est_cam_rect

PIN_HINT = 'pin it in clip.yml: pip install "opencv-python-headless>=4.9,<5"'
SAMPLE_FPS = 2
SMALL_W = 960
# where the cam face is searched, per corner (fractions x0, y0, x1, y1)
CORNER_SEARCH = {"bottom-left": (0.0, 0.45, 0.40, 1.0), "bottom-right": (0.60, 0.45, 1.0, 1.0),
                 "top-left": (0.0, 0.0, 0.40, 0.55), "top-right": (0.60, 0.0, 1.0, 0.55)}
MIN_FACE_HITS = 0.35        # share of samples with a face for the cam to count as verified


class DetectorUnavailable(RuntimeError):
    """The installed OpenCV cannot run a detector we need. The message says why and how to fix it."""


def _dig(obj, dotted):
    for part in dotted.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def cascade_class(cv2):
    """cv2.CascadeClassifier, or the same class under another module name (OpenCV 5 moved things
    around). None when no build we know of has it."""
    for name in ("CascadeClassifier", "objdetect.CascadeClassifier", "xobjdetect.CascadeClassifier"):
        cls = _dig(cv2, name)
        if cls is not None:
            return cls
    return None


def haar_face_path(cv2):
    base = _dig(cv2, "data.haarcascades")
    return None if base is None else base + "haarcascade_frontalface_default.xml"


def load_face_cascade(cv2=None):
    """Face cascade or a clear DetectorUnavailable (never an AttributeError mid-render)."""
    if cv2 is None:
        import cv2
    ver = getattr(cv2, "__version__", "?")
    cls = cascade_class(cv2)
    if cls is None:
        raise DetectorUnavailable(f"OpenCV {ver} has no CascadeClassifier (OpenCV 5 removed it); " + PIN_HINT)
    path = haar_face_path(cv2)
    if path is None:
        raise DetectorUnavailable(f"OpenCV {ver} ships no haarcascade data (cv2.data missing); " + PIN_HINT)
    casc = cls(path)
    empty = getattr(casc, "empty", None)
    if callable(empty) and empty():
        raise DetectorUnavailable(f"OpenCV {ver}: face cascade failed to load from {path}")
    return casc


def load_people_detector(cv2=None):
    """HOG people detector or a clear DetectorUnavailable."""
    if cv2 is None:
        import cv2
    ver = getattr(cv2, "__version__", "?")
    if _dig(cv2, "HOGDescriptor") is None or _dig(cv2, "HOGDescriptor_getDefaultPeopleDetector") is None:
        raise DetectorUnavailable(f"OpenCV {ver} has no HOG people detector; " + PIN_HINT)
    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    return hog


def check_features(cv2=None):
    """Fail-fast feature check for CI. Returns (ok, report lines). No video needed."""
    if cv2 is None:
        import cv2
    ver = str(getattr(cv2, "__version__", "?"))
    lines, ok = [f"opencv {ver}"], True
    major = ver.split(".")[0]
    if major.isdigit() and int(major) >= 5:
        lines.append("FAIL opencv major >= 5: " + PIN_HINT)
        ok = False
    for label, fn in (("face cascade", load_face_cascade), ("people detector", load_people_detector)):
        try:
            fn(cv2)
            lines.append("ok   " + label)
        except DetectorUnavailable as e:
            lines.append(f"FAIL {label}: {e}")
            ok = False
    for name in ("GaussianBlur", "addWeighted", "warpAffine", "resize", "equalizeHist"):
        if _dig(cv2, name) is None:
            lines.append(f"FAIL cv2.{name} missing")
            ok = False
    return ok, lines


def frames_iter(media, start, dur, fps, w, h):
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", media, "-t", f"{dur:.3f}",
           "-an", "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size, i = w * h * 3, 0
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield i / fps, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            i += 1
    finally:
        p.stdout.close()
        p.wait()


def best_face(cascade, gray):
    import cv2
    g = cv2.equalizeHist(gray)
    m = max(30, int(0.10 * gray.shape[1]))
    f = cascade.detectMultiScale(g, scaleFactor=1.1, minNeighbors=5, minSize=(m, m))
    if len(f) == 0:
        return None
    return max(f, key=lambda r: r[2] * r[3])


def sample(media, start, dur, src_w, src_h, corners, fps=SAMPLE_FPS, small_w=SMALL_W):
    """One decode pass at `fps`: small colour frames for the subject tracker, and the best face
    found in each searched corner. faces[corner] = [(t, cx_px, cy_px, face_w_px)] in source px."""
    import cv2
    casc = load_face_cascade(cv2)
    sh = int(round(src_h * small_w / src_w))
    times, small = [], []
    faces = {c: [] for c in corners}
    for t, fr in frames_iter(media, start, dur, fps, src_w, src_h):
        times.append(t)
        small.append(cv2.resize(fr, (small_w, sh), interpolation=cv2.INTER_AREA))
        for c in corners:
            x0, y0, x1, y1 = region_px(CORNER_SEARCH[c], src_w, src_h)
            f = best_face(casc, cv2.cvtColor(fr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY))
            if f is not None:
                faces[c].append((t, x0 + f[0] + f[2] / 2, y0 + f[1] + f[3] / 2, float(f[2])))
    return {"times": times, "small": small, "faces": faces, "n": len(times)}


def _extra_cascades(cv2):
    """Other stock cascades for small or stylised cams (alt2 frontal, profile). Missing ones are skipped."""
    out = []
    base = _dig(cv2, "data.haarcascades")
    cls = cascade_class(cv2)
    if base is None or cls is None:
        return out
    for name in ("haarcascade_frontalface_alt2.xml", "haarcascade_profileface.xml"):
        try:
            c = cls(base + name)
            empty = getattr(c, "empty", None)
            if not (callable(empty) and empty()):
                out.append(c)
        except Exception:
            pass
    return out


def wide_face(cascades, gray, min_frac=0.025):
    """Relaxed, wide-scale face search for small or stylised cams: tiny minimum size (a cam can be a small
    inset), fine scale steps, few neighbours, several cascades, also on a 2x upscaled copy.
    Returns the largest (x, y, w, h) in `gray` pixels or None. Used for diagnostics and for a cam region the
    person pointed at; the strict detector (best_face) still decides the automatic cam."""
    import cv2
    g = cv2.equalizeHist(gray)
    m = max(18, int(min_frac * g.shape[1]))
    best = None
    for casc in cascades:
        for img, k in ((g, 1.0), (cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC), 0.5)):
            f = casc.detectMultiScale(img, scaleFactor=1.03, minNeighbors=2, minSize=(int(m / k), int(m / k)))
            for r in f:
                r = tuple(float(v) * k for v in r)
                if best is None or r[2] * r[3] > best[2] * best[3]:
                    best = r
    return best


def faces_in_rect(media, start, dur, rect, src_w, src_h, fps=2):
    """Faces found by the relaxed detector inside a person-given cam rect (fractions).
    Returns (obs [(t, cx, cy, face_w)] in source px, frames_seen)."""
    import cv2
    cascades = [load_face_cascade(cv2)] + _extra_cascades(cv2)
    x0, y0, x1, y1 = region_px(rect, src_w, src_h)
    obs, n = [], 0
    for t, fr in frames_iter(media, start, dur, fps, src_w, src_h):
        n += 1
        f = wide_face(cascades, cv2.cvtColor(fr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY))
        if f is not None:
            obs.append((t, x0 + f[0] + f[2] / 2, y0 + f[1] + f[3] / 2, float(f[2])))
    return obs, n


def cam_diag(media, start, dur, src_w, src_h, out_jpg=None, fps=0.25, max_frames=16, seed_rect=None):
    """Why was no facecam found? Per corner: frames with a face under the strict settings used for the cam
    decision, relaxed settings, and the wide-scale search (tiny min size, several cascades), plus the largest
    face seen. Optional seed_rect (fractions, from a person) gets the same counts. Writes a contact sheet of
    FULL source frames spread over the moment (6 tiles of 960x540, so a person can look at the stream layout)
    and up to 3 native-resolution frames next to it (source_frame_N.jpg). Diagnostic only: the automatic cam
    decision is unchanged."""
    import cv2
    casc = load_face_cascade(cv2)
    cascades = [casc] + _extra_cascades(cv2)
    rep = {c: {"strict": 0, "relaxed": 0, "wide": 0, "max_face_px": 0} for c in CORNER_SEARCH}
    seed = {"rect": [round(float(v), 4) for v in seed_rect], "strict": 0, "wide": 0, "max_face_px": 0} if seed_rect else None
    n_expect = max(1, min(max_frames, int(dur * fps)))
    native_at = {0, n_expect // 2, n_expect - 1}
    thumbs, n, native = [], 0, []
    for t, fr in frames_iter(media, start, dur, fps, src_w, src_h):
        n += 1
        thumbs.append(cv2.resize(fr, (960, int(round(960 * src_h / src_w))), interpolation=cv2.INTER_AREA))
        if out_jpg and (n - 1) in native_at and len(native) < 3:
            native.append(fr.copy())
        for c, box in CORNER_SEARCH.items():
            x0, y0, x1, y1 = region_px(box, src_w, src_h)
            gray = cv2.cvtColor(fr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
            if best_face(casc, gray) is not None:
                rep[c]["strict"] += 1
            g = cv2.equalizeHist(gray)
            m = max(24, int(0.04 * g.shape[1]))
            f = casc.detectMultiScale(g, scaleFactor=1.05, minNeighbors=3, minSize=(m, m))
            if len(f):
                rep[c]["relaxed"] += 1
                rep[c]["max_face_px"] = max(rep[c]["max_face_px"], int(max(r[2] for r in f)))
            w = wide_face(cascades, gray)
            if w is not None:
                rep[c]["wide"] += 1
                rep[c]["max_face_px"] = max(rep[c]["max_face_px"], int(w[2]))
        if seed:
            x0, y0, x1, y1 = region_px(seed_rect, src_w, src_h)
            gray = cv2.cvtColor(fr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
            seed["strict"] += best_face(casc, gray) is not None
            w = wide_face(cascades, gray)
            if w is not None:
                seed["wide"] += 1
                seed["max_face_px"] = max(seed["max_face_px"], int(w[2]))
        if n >= max_frames:
            break
    if out_jpg and thumbs:
        pick = sorted({int(round(i * (len(thumbs) - 1) / 5)) for i in range(6)}) if len(thumbs) > 1 else [0]
        tiles = [thumbs[i] for i in pick]
        h, w = thumbs[0].shape[:2]
        rows = (len(tiles) + 1) // 2
        sheet = np.zeros((h * rows, w * 2, 3), np.uint8)
        for i, th in enumerate(tiles):
            sheet[(i // 2) * h:(i // 2 + 1) * h, (i % 2) * w:(i % 2 + 1) * w] = th
        cv2.imwrite(out_jpg, sheet, [cv2.IMWRITE_JPEG_QUALITY, 80])
        stem = out_jpg.rsplit("source_contact", 1)[0] if "source_contact" in out_jpg else out_jpg.rsplit(".", 1)[0] + "_"
        for i, fr in enumerate(native):
            cv2.imwrite(f"{stem}source_frame_{i}.jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 88])
    for v in rep.values():
        v["strict_pct"] = round(100 * v["strict"] / n, 1) if n else 0.0
        v["wide_pct"] = round(100 * v["wide"] / n, 1) if n else 0.0
    out = {"frames": n, "window_s": [round(start, 2), round(start + dur, 2)], "source": [src_w, src_h],
           "corners": rep, "min_hits_needed_pct": round(100 * MIN_FACE_HITS)}
    if seed:
        seed["wide_pct"] = round(100 * seed["wide"] / n, 1) if n else 0.0
        out["seed"] = seed
    return out


def choose_cam(faces, n, src_w, src_h, min_hits=MIN_FACE_HITS, max_spread=0.2):
    """Pick the corner whose face detections are frequent AND stable (a real cam stays put; a
    game character does not). Returns {corner, obs, hit_pct, rect} or None, plus a stats dict."""
    stats, best = {}, None
    for c, obs in faces.items():
        hit = len(obs) / n if n else 0.0
        reg = region_px(CORNER_SEARCH[c], src_w, src_h)
        spread = 1.0
        if len(obs) >= 3:
            sx = float(np.median(np.abs(np.array([o[1] for o in obs]) - np.median([o[1] for o in obs]))))
            sy = float(np.median(np.abs(np.array([o[2] for o in obs]) - np.median([o[2] for o in obs]))))
            spread = max(sx / (reg[2] - reg[0]), sy / (reg[3] - reg[1]))
        stats[c] = {"hit_pct": round(100 * hit, 1), "spread": round(spread, 3)}
        if len(obs) >= 3 and hit >= min_hits and spread <= max_spread and (best is None or hit > best[1]):
            best = (c, hit, obs)
    if not best:
        return None, stats
    c, hit, obs = best
    return {"corner": c, "obs": obs, "hit_pct": round(100 * hit, 1),
            "rect": est_cam_rect(c, obs, src_w, src_h)}, stats


_HOG = None


def detect_people(img, mask=None, min_score=0.4):
    """HOG people detector on a small BGR frame. mask = (x0, y0, x1, y1) fractions to blank out
    (the facecam). Returns normalised boxes (x0, y0, x1, y1, score)."""
    import cv2
    global _HOG
    if _HOG is None:
        _HOG = load_people_detector(cv2)
    h, w = img.shape[:2]
    im = img
    if mask:
        im = img.copy()
        im[int(mask[1] * h):int(np.ceil(mask[3] * h)), int(mask[0] * w):int(np.ceil(mask[2] * w))] = 127
    rects, weights = _HOG.detectMultiScale(im, winStride=(8, 8), padding=(8, 8), scale=1.05)
    out = []
    for (x, y, bw, bh), s in zip(rects, np.ravel(weights)):
        if s >= min_score:
            out.append((x / w, y / h, (x + bw) / w, (y + bh) / h, float(s)))
    return out


def small_gray(frames, w=64, h=36):
    import cv2
    return np.stack([cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (w, h), interpolation=cv2.INTER_AREA)
                     for f in frames]) if frames else np.zeros((0, h, w), np.uint8)
