"""Object detector for the subject track: YOLOX-s (COCO) through onnxruntime. Person plus car, truck, motorcycle, bicycle.

Pure numpy pre/post-processing (unit-tested with synthetic output); the model file is downloaded once from a pinned URL
and refused unless its SHA-256 matches the pin (same pattern as the SR models in cam_sr)."""
import hashlib
import os
import urllib.request

import numpy as np

MODEL = {"file": "yolox_s.onnx", "size": 35858002,
         "url": "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx",
         "sha256": "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"}
INPUT = 640
STRIDES = (8, 16, 32)
CLASSES = {0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 7: "truck"}   # COCO ids kept
VEHICLES = ("bicycle", "car", "motorcycle", "truck")
CONF = 0.30
NMS_IOU = 0.45


class ModelError(RuntimeError):
    pass


def default_cache():
    return os.environ.get("CLIP_DET_CACHE", os.path.expanduser("~/.cache/clip-engine/det"))


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(cache_dir=None, opener=None):
    """Path of the verified model file; downloads it once. A file with another hash is refused."""
    cache_dir = cache_dir or default_cache()
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, MODEL["file"])
    if os.path.isfile(path) and sha256_of(path) == MODEL["sha256"]:
        return path
    opener = opener or (lambda u: urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": "clip-engine"}), timeout=180).read())
    try:
        data = opener(MODEL["url"])
    except Exception as e:
        raise ModelError(f"yolox: download failed ({type(e).__name__}: {str(e)[:100]})")
    got = hashlib.sha256(data).hexdigest()
    if got != MODEL["sha256"]:
        raise ModelError(f"yolox: sha256 {got[:12]} does not match the pinned {MODEL['sha256'][:12]}")
    with open(path, "wb") as f:
        f.write(data)
    return path


def letterbox(img, size=INPUT):
    """BGR uint8 (h, w, 3) -> (float32 CHW 1x3xSxS, ratio). Grey 114 padding at the bottom/right, as YOLOX does."""
    import cv2
    h, w = img.shape[:2]
    r = min(size / h, size / w)
    nh, nw = max(1, int(round(h * r))), max(1, int(round(w * r)))
    res = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, np.uint8)
    canvas[:nh, :nw] = res
    return np.ascontiguousarray(canvas.transpose(2, 0, 1)[None].astype(np.float32)), r


def grids(size=INPUT, strides=STRIDES):
    """(N, 2) cell offsets and (N, 1) strides for the flattened YOLOX output."""
    gs, ss = [], []
    for s in strides:
        n = size // s
        ys, xs = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        gs.append(np.stack([xs, ys], -1).reshape(-1, 2))
        ss.append(np.full((n * n, 1), s))
    return np.concatenate(gs).astype(np.float32), np.concatenate(ss).astype(np.float32)


def nms(boxes, scores, iou=NMS_IOU):
    order = np.argsort(-scores)
    keep = []
    while len(order):
        i = order[0]
        keep.append(int(i))
        if len(order) == 1:
            break
        rest = order[1:]
        xx0 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx1 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx1 - xx0, 0, None) * np.clip(yy1 - yy0, 0, None)
        a = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        b = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        order = rest[inter / np.maximum(a + b - inter, 1e-9) <= iou]
    return keep


def decode(raw, ratio, img_w, img_h, conf=CONF, size=INPUT):
    """Raw YOLOX output (1, N, 85) -> normalised boxes [(x0, y0, x1, y1, score, label)] for the wanted classes."""
    out = np.asarray(raw, dtype=np.float32)[0].copy()
    g, s = grids(size)
    if len(out) != len(g):
        raise ValueError(f"detector output has {len(out)} cells, expected {len(g)}")
    out[:, :2] = (out[:, :2] + g) * s
    out[:, 2:4] = np.exp(out[:, 2:4]) * s
    obj, cls = out[:, 4], out[:, 5:]
    res = []
    for cid, name in CLASSES.items():
        sc = obj * cls[:, cid]
        m = sc >= conf
        if not m.any():
            continue
        cx, cy, w, h = out[m, 0], out[m, 1], out[m, 2], out[m, 3]
        bx = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], 1) / ratio
        bx[:, [0, 2]] = np.clip(bx[:, [0, 2]], 0, img_w)
        bx[:, [1, 3]] = np.clip(bx[:, [1, 3]], 0, img_h)
        ssc = sc[m]
        for k in nms(bx, ssc):
            b = bx[k]
            if b[2] - b[0] < 2 or b[3] - b[1] < 2:
                continue
            res.append((float(b[0] / img_w), float(b[1] / img_h), float(b[2] / img_w), float(b[3] / img_h),
                        float(ssc[k]), name))
    return res


_SESSION = None


def session(cache_dir=None):
    global _SESSION
    if _SESSION is None:
        import onnxruntime as ort
        _SESSION = ort.InferenceSession(ensure_model(cache_dir), providers=["CPUExecutionProvider"])
    return _SESSION


def detect(img, conf=CONF, sess=None):
    """BGR frame -> normalised boxes (x0, y0, x1, y1, score, label)."""
    sess = sess or session()
    h, w = img.shape[:2]
    x, r = letterbox(img)
    raw = sess.run(None, {sess.get_inputs()[0].name: x})[0]
    return decode(raw, r, w, h, conf)
