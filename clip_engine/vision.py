"""OpenCV detectors (lazy import): the streamer's face for the cam block, a person for the subject
track. Everything that can be pure (corner choice, region maths) is pure and unit-tested."""
import subprocess

import numpy as np

from .layout import region_px, est_cam_rect

SAMPLE_FPS = 2
SMALL_W = 960
# where the cam face is searched, per corner (fractions x0, y0, x1, y1)
CORNER_SEARCH = {"bottom-left": (0.0, 0.45, 0.40, 1.0), "bottom-right": (0.60, 0.45, 1.0, 1.0),
                 "top-left": (0.0, 0.0, 0.40, 0.55), "top-right": (0.60, 0.0, 1.0, 0.55)}
MIN_FACE_HITS = 0.35        # share of samples with a face for the cam to count as verified


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
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
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
        _HOG = cv2.HOGDescriptor()
        _HOG.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
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
