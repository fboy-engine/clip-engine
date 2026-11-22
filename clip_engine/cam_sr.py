"""Cam block upscalers, the benchmark scoring, and the choice of the winner by measurement.

The cam block is ALWAYS 1080x540, made from the (about) 360x180 crop of the streamer cam: a 3x upscale. The
candidates are the lanczos baseline, OpenCV dnn_superres FSRCNN and EDSR (x3 models) and the Real-ESRGAN
general-x4v3 ONNX model on CPU (4x, then area-resized to 3x). No face-restoration models: they change the face.
"""
import hashlib
import os
import time
import urllib.request

import numpy as np

from . import quality

SCALE = 3
ROI_MARGIN = 6                  # px of source around the 360x180 box that the upscaler also sees (sub-pixel crop)
CAM_FPS_TIERS = (30, 24)        # cam is processed at this rate; each processed frame is reused for the frames between
SR_BUDGET_SEC = 720             # share of the 35-minute render step the upscaler may use (whisper, tracking, encode need the rest)
SAFETY = 1.25                   # measured time per frame is multiplied by this before it is compared with the budget
MAX_CLIP_SEC = 45.0
DENOISE_VF = "hqdn3d=1.2:1.0:3.0:2.5"                     # light spatial + temporal denoise (luma/chroma spatial, luma/chroma temporal)
# ffmpeg filters tried on the FULL frame before the cam crop. The static ffmpeg build has no `pp` (libpostproc was
# dropped), so the deblock family is: deblock, spp/pp7 (the postproc ports) with a fixed QP since the decoder gives none.
DEBLOCK_VARIANTS = {
    "none": "",
    "deblock_weak": "deblock=filter=weak:block=8",
    "deblock_strong": "deblock=filter=strong:block=8",
    "spp": "spp=quality=4:qp=8",
    "pp7": "pp7=qp=8:mode=medium",
}
DEBLOCK_MIN_SSIM = 0.93         # a deblock variant must keep this SSIM against the untouched crop (no smearing)
DEBLOCK_MIN_GAIN = 0.05         # and cut blockiness by at least 5 % to be used at all
UNSHARP_AMOUNT, UNSHARP_SIGMA = 0.25, 1.0                 # gentle
GRAIN_SIGMA = 1.1               # very light luma grain, 8-bit levels

HF = "https://huggingface.co/momoapps/optimix-models/resolve/main/realesr-general-x4v3.onnx"
MODELS = {
    "fsrcnn": {"file": "FSRCNN_x3.pb", "algo": "fsrcnn", "size": 40093,
               "url": "https://github.com/Saafke/FSRCNN_Tensorflow/raw/master/models/FSRCNN_x3.pb",
               "sha256": "efd38655a815908c6c8954db6052f128e76a735f1de657894c477d0dc0b64481"},
    "edsr": {"file": "EDSR_x3.pb", "algo": "edsr", "size": 38524784,
             "url": "https://github.com/Saafke/EDSR_Tensorflow/raw/master/models/EDSR_x3.pb",
             "sha256": "3baa3740fdb8ee9c52f1a41d69fa74cb9feef0fa9bfeec24f0ee58b928068e9a"},
    "realesrgan": {"file": "realesr-general-x4v3.onnx", "size": 4866420, "url": HF,
                   "sha256": "7b10e80021ec3e0ff35017ae5022791879d93a799b2e201e1010a37510202266"},
}
CANDIDATES = ("lanczos", "fsrcnn", "edsr", "realesrgan")


class ModelError(RuntimeError):
    pass


def default_cache():
    return os.environ.get("CLIP_SR_CACHE", os.path.expanduser("~/.cache/clip-engine/sr"))


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(key, cache_dir=None, opener=None):
    """Path of a verified model file; downloads it once. The SHA-256 is pinned: a changed file is refused."""
    m = MODELS[key]
    cache_dir = cache_dir or default_cache()
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, m["file"])
    if os.path.isfile(path) and sha256_of(path) == m["sha256"]:
        return path
    opener = opener or (lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "clip-engine"}),
                                                          timeout=120).read())
    try:
        data = opener(m["url"])
    except Exception as e:
        raise ModelError(f"{key}: download failed ({type(e).__name__}: {str(e)[:100]})")
    got = hashlib.sha256(data).hexdigest()
    if got != m["sha256"]:
        raise ModelError(f"{key}: sha256 {got[:12]} does not match the pinned {m['sha256'][:12]}")
    with open(path, "wb") as f:
        f.write(data)
    return path


# ---- upscalers: BGR uint8 in, BGR uint8 out at exactly 3x -------------------------------------------------

class Lanczos:
    name = "lanczos"

    def load(self, cache_dir=None, opener=None):
        return self

    def run(self, img):
        import cv2
        return cv2.resize(img, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_LANCZOS4)


class DnnSR:
    def __init__(self, name):
        self.name = name

    def load(self, cache_dir=None, opener=None):
        import cv2
        if not hasattr(cv2, "dnn_superres"):
            raise ModelError("cv2.dnn_superres missing (install opencv-contrib-python-headless>=4.9,<5)")
        path = ensure_model(self.name, cache_dir, opener)
        sr = cv2.dnn_superres.DnnSuperResImpl_create()
        sr.readModel(path)
        sr.setModel(MODELS[self.name]["algo"], SCALE)
        self.sr = sr
        return self

    def run(self, img):
        return self.sr.upsample(img)


class OnnxSR:
    name = "realesrgan"

    def load(self, cache_dir=None, opener=None):
        try:
            import onnxruntime as ort
        except ImportError:
            raise ModelError("onnxruntime is not installed")
        path = ensure_model("realesrgan", cache_dir, opener)
        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, os.cpu_count() or 1)
        self.sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        return self

    def run(self, img):
        import cv2
        rgb = np.ascontiguousarray(img[..., ::-1]).astype(np.float32) / 255.0
        y = self.sess.run(None, {self.inp: rgb.transpose(2, 0, 1)[None]})[0][0]
        out = (np.clip(y, 0, 1).transpose(1, 2, 0) * 255.0 + 0.5).astype(np.uint8)[..., ::-1]
        h, w = img.shape[:2]
        return cv2.resize(np.ascontiguousarray(out), (w * SCALE, h * SCALE), interpolation=cv2.INTER_AREA)   # 4x -> 3x


def make(name):
    if name == "lanczos":
        return Lanczos()
    if name in ("fsrcnn", "edsr"):
        return DnnSR(name)
    if name == "realesrgan":
        return OnnxSR()
    raise ValueError(name)


# ---- scoring ----------------------------------------------------------------------------------------------

def score_pair(gt, out, grid=8, ox=0, oy=0):
    """gt and out have the same size (the upscaled picture is compared with the original crop)."""
    return {"psnr": round(quality.psnr(gt, out), 3), "ssim": round(quality.ssim(gt, out), 4),
            "blockiness": round(quality.blockiness(out, grid, ox, oy), 4),
            "edge": round(quality.edge_strength(out), 2)}


def time_run(up, img, n=3):
    """Median seconds per call after one warm-up call."""
    up.run(img)
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        up.run(img)
        ts.append(time.perf_counter() - t)
    return float(np.median(ts))


def bench_candidate(up, crops, roi_img, log=print):
    """crops: [{"img": BGR HxW (both divisible by 3), "ox", "oy"}]: each is shrunk 3x, upscaled 3x with `up`
    and scored against the original. roi_img: a real production-size input (box + margin) for the timing,
    which is measured FIRST."""
    import cv2
    sec = time_run(up, roi_img)
    log(f"{up.name}: {sec:.3f} s per frame on a {roi_img.shape[1]}x{roi_img.shape[0]} input")
    rows = []
    for c in crops:
        gt = c["img"]
        h, w = gt.shape[:2]
        lo = cv2.resize(gt, (w // SCALE, h // SCALE), interpolation=cv2.INTER_AREA)
        out = up.run(lo)
        if out.shape[:2] != gt.shape[:2]:
            raise ModelError(f"{up.name}: output {out.shape[:2]} != {gt.shape[:2]}")
        rows.append(score_pair(gt, out, 8, c.get("ox", 0), c.get("oy", 0)))
    res = {k: round(float(np.mean([r[k] for r in rows])), 4) for k in rows[0]}
    res["frames_scored"] = len(rows)
    res["sec_per_frame"] = round(sec, 4)
    res["roi_px"] = [int(roi_img.shape[1]), int(roi_img.shape[0])]
    return res


def frames_needed(cam_fps, max_sec=MAX_CLIP_SEC):
    return int(np.ceil(max_sec * cam_fps))


def fit_fps(sec_per_frame, budget=SR_BUDGET_SEC, tiers=CAM_FPS_TIERS):
    """Highest cam frame rate whose total upscaling time (with the safety factor) fits the budget, else None."""
    for fps in tiers:
        if sec_per_frame * SAFETY * frames_needed(fps) <= budget:
            return fps
    return None


def choose(results, budget=SR_BUDGET_SEC):
    """The winner by measurement: the best SSIM (then PSNR) among candidates that (1) fit the time budget at 30 or
    24 fps, and (2) do not lose to the lanczos baseline on PSNR, SSIM or blockiness. Lanczos wins otherwise."""
    base = results["lanczos"]
    pool, why = [], {}
    for name, r in results.items():
        if name == "lanczos":
            continue
        if r.get("error"):
            why[name] = "unavailable: " + r["error"]
            continue
        fps = fit_fps(r["sec_per_frame"], budget)
        if fps is None:
            why[name] = (f"too slow: {r['sec_per_frame']:.2f} s/frame x {frames_needed(CAM_FPS_TIERS[-1])} frames "
                         f"x {SAFETY} > {budget:g} s budget")
            continue
        if r["ssim"] < base["ssim"] or r["psnr"] < base["psnr"] or r["blockiness"] > base["blockiness"] * 1.05:
            why[name] = (f"not better than lanczos (psnr {r['psnr']} vs {base['psnr']}, ssim {r['ssim']} vs "
                         f"{base['ssim']}, blockiness {r['blockiness']} vs {base['blockiness']})")
            continue
        pool.append((r["ssim"], r["psnr"], name, fps))
    if not pool:
        return {"method": "lanczos", "cam_fps": CAM_FPS_TIERS[0], "rejected": why,
                "reason": "no neural upscaler beat lanczos inside the time budget"}
    ssim_, psnr_, name, fps = max(pool)
    return {"method": name, "cam_fps": fps, "rejected": why, "ssim": ssim_, "psnr": psnr_,
            "reason": f"best SSIM among the candidates that fit {budget:g} s at {fps} fps"}


def choose_deblock(rows, min_ssim=DEBLOCK_MIN_SSIM, min_gain=DEBLOCK_MIN_GAIN):
    """rows: {variant: {"blockiness", "ssim"}} measured on the real crops, "none" included. Returns the variant
    with the lowest blockiness that keeps SSIM >= min_ssim and cuts blockiness by >= min_gain; else "none"."""
    base = rows["none"]["blockiness"]
    best, best_b = "none", base
    for name, r in rows.items():
        if name == "none" or r.get("error"):
            continue
        if r["ssim"] >= min_ssim and r["blockiness"] <= base * (1 - min_gain) and r["blockiness"] < best_b:
            best, best_b = name, r["blockiness"]
    return best


# ---- the finishing step ------------------------------------------------------------------------------------

def sharpen(img, amount=UNSHARP_AMOUNT, sigma=UNSHARP_SIGMA):
    import cv2
    blur = cv2.GaussianBlur(img, (0, 0), sigma)
    return cv2.addWeighted(img, 1 + amount, blur, -amount, 0)


def grain(img, seed, sigma=GRAIN_SIGMA):
    """Very light luma-only grain (same noise on B, G, R, so no colour speckle); a new pattern every frame."""
    rng = np.random.default_rng(seed)
    n = rng.normal(0.0, sigma, img.shape[:2]).astype(np.float32)[..., None]
    return np.clip(np.rint(img.astype(np.float32) + n), 0, 255).astype(np.uint8)


def finish(img, seed=0):
    return grain(sharpen(img), seed)


def chain_description(deblock, upscaler, cam_fps):
    return {"deblock": deblock, "deblock_vf": DEBLOCK_VARIANTS.get(deblock, ""), "denoise_vf": DENOISE_VF,
            "upscaler": upscaler, "cam_fps": cam_fps, "sharpen": {"amount": UNSHARP_AMOUNT, "sigma": UNSHARP_SIGMA},
            "grain_sigma": GRAIN_SIGMA}
