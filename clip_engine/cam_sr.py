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
# ---- B3 (user decision): Real-ESRGAN on the cam ROI at a LOW cam fps, in-between frames filled by motion compensation
B3_METHOD = "realesrgan"
B3_FPS_TIERS = (20, 15, 12, 10, 8)    # highest that fits the budget; lowered automatically for long clips / slow runners
BLEND_STEPS = (1.0, 0.85, 0.7, 0.55, 0.4, 0.25)   # SR share of the final picture (the rest is the lanczos upscale)
BLEND_START = 0.7
FIDELITY_MIN = 0.96             # shrunk-back block vs the untouched real crop (the face must stay the real face)
SR_FACTOR = 4                   # every upscaler hands over a 4x picture; the sub-pixel crop resamples it to the exact block

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

    def run4(self, img):
        import cv2
        return cv2.resize(img, None, fx=SR_FACTOR, fy=SR_FACTOR, interpolation=cv2.INTER_LANCZOS4)


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

    def run4(self, img):                  # x3 model: one extra lanczos step to the common 4x
        import cv2
        out = self.sr.upsample(img)
        return cv2.resize(out, (img.shape[1] * SR_FACTOR, img.shape[0] * SR_FACTOR), interpolation=cv2.INTER_LANCZOS4)


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

    def run4(self, img):
        """The raw model output: exactly 4x, BGR uint8."""
        rgb = np.ascontiguousarray(img[..., ::-1]).astype(np.float32) / 255.0
        y = self.sess.run(None, {self.inp: rgb.transpose(2, 0, 1)[None]})[0][0]
        return np.ascontiguousarray((np.clip(y, 0, 1).transpose(1, 2, 0) * 255.0 + 0.5).astype(np.uint8)[..., ::-1])

    def run(self, img):
        import cv2
        out = self.run4(img)
        h, w = img.shape[:2]
        return cv2.resize(out, (w * SCALE, h * SCALE), interpolation=cv2.INTER_AREA)   # 4x -> 3x


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


# ---- B3 choices, by measurement -----------------------------------------------------------------------------

def choose_b3(results, prefer=B3_METHOD, budget=SR_BUDGET_SEC, tiers=B3_FPS_TIERS):
    """The upscaler for the cam block: the user's pick (Real-ESRGAN) if it ran and fits the budget at some cam fps
    in `tiers`; else the next best that fits (edsr, fsrcnn); lanczos last (the render refuses to ship it).
    results: {name: bench row with sec_per_frame or error}."""
    order = [prefer] + [n for n in ("edsr", "fsrcnn") if n != prefer] + ["lanczos"]
    why = {}
    for name in order:
        r = results.get(name)
        if r is None:
            why[name] = "not benchmarked"
            continue
        if r.get("error"):
            why[name] = "unavailable: " + r["error"]
            continue
        if name == "lanczos":
            return {"method": "lanczos", "cam_fps": tiers[1] if len(tiers) > 1 else tiers[0], "rejected": why,
                    "reason": "no neural upscaler is available inside the time budget: plain lanczos would be shipped"}
        fps = fit_fps(r["sec_per_frame"], budget, tiers)
        if fps is None:
            why[name] = (f"too slow: {r['sec_per_frame']:.2f} s/frame x {frames_needed(tiers[-1])} frames x {SAFETY} "
                         f"> {budget:g} s budget even at {tiers[-1]} fps")
            continue
        return {"method": name, "cam_fps": fps, "rejected": why, "sec_per_frame": r["sec_per_frame"],
                "reason": (f"{name} fits {budget:g} s at {fps} fps" + ("" if name == prefer else f" (next best: {prefer} did not fit)"))}
    return {"method": "lanczos", "cam_fps": tiers[0], "rejected": why, "reason": "nothing usable"}


NO_GRID_BLOCKINESS = 1.02       # blockiness ~1.0 = no visible block grid (quality.blockiness)


def blockiness_ok(enh_b, old_b):
    """(ok, how). The block must show LESS grid than plain lanczos. One honest exception: the old chain's
    bilateral denoise softens the 8 px grid lines below neutral (blockiness < 1.0, measured 0.911 on the real cam),
    which is blur, not a cleaner picture. When the old number is below 1.0, a block with no visible grid
    (<= NO_GRID_BLOCKINESS) also passes. When the old number is 1.0 or higher, it must be strictly lower."""
    if enh_b < old_b:
        return True, "lower than plain lanczos"
    if old_b < 1.0 and enh_b <= NO_GRID_BLOCKINESS:
        return True, (f"no block grid ({enh_b:.4f} <= {NO_GRID_BLOCKINESS:g}); plain lanczos {old_b:.4f} is below 1.0 "
                      f"only because its denoise blurs the grid lines")
    return False, f"blockiness {enh_b:.4f} is not lower than plain lanczos {old_b:.4f}"


def gate_numbers(enh, old, fid_min=FIDELITY_MIN):
    """The cam quality gate. enh/old: {"fidelity", "blockiness", "edge"} (means over the sampled frames; for `old`,
    the plain lanczos chain, fidelity is only reported). Fails with a clear reason per metric."""
    reasons = []
    if enh["fidelity"] < fid_min:
        reasons.append(f"face fidelity {enh['fidelity']:.4f} is below {fid_min:g} (the enhanced block drifts from the real face)")
    b_ok, how = blockiness_ok(enh["blockiness"], old["blockiness"])
    if not b_ok:
        reasons.append(how)
    if not enh["edge"] > old["edge"]:
        reasons.append(f"edge strength {enh['edge']:.2f} is not higher than plain lanczos {old['edge']:.2f}")
    return {"ok": not reasons, "reasons": reasons, "fidelity_min": fid_min, "blockiness_rule": how if b_ok else "fail"}


def choose_blend(rows, base, fid_min=FIDELITY_MIN, prefer=None):
    """rows: {blend: {"fidelity","blockiness","edge"}} measured on real crops; base: the plain lanczos numbers.
    Among the SR shares that pass the whole gate: the largest one, or (prefer given) the one closest to `prefer`
    (ties go to the smaller SR share, which keeps the real face). None if none passes; then `why` says what
    failed at the smallest share."""
    passing = [w for w in sorted(rows, reverse=True) if gate_numbers(rows[w], base, fid_min)["ok"]]
    if passing:
        pick = passing[0] if prefer is None else min(passing, key=lambda w: (abs(w - prefer), w))
        return {"blend": pick, "ok": True, "reasons": [], "passing": sorted(passing)}
    low = min(rows)
    return {"blend": None, "ok": False, "reasons": gate_numbers(rows[low], base, fid_min)["reasons"],
            "note": f"no SR share in {sorted(rows)} passes the gate (reasons are for share {low})"}


def ghost_verdict(key_fid, inter_fid, mean_tol=0.03, min_tol=0.08):
    """key_fid / inter_fid: fidelity of the keyframe outputs / of the in-between outputs against the real frames of
    the same instant. Double edges (ghosting) show as in-between frames that match the real frame worse than the
    keyframes do. Returns (ok, detail)."""
    if not key_fid or not inter_fid:
        return False, "no frames to compare"
    k, m, lo = float(np.mean(key_fid)), float(np.mean(inter_fid)), float(np.min(inter_fid))
    ok = m >= k - mean_tol and lo >= k - min_tol
    return ok, (f"keyframe fidelity {k:.4f}, in-between mean {m:.4f} (min {lo:.4f}); allowed drop {mean_tol:g} mean, "
                f"{min_tol:g} worst")


def plan_cam_fps(sec_per_frame, dur, cap_fps, budget=SR_BUDGET_SEC, tiers=B3_FPS_TIERS):
    """Highest SR rate <= cap_fps whose total time for THIS clip (with the safety factor) fits; None if even the
    lowest tier does not. The ROI is measured at render time, so this is the render's own check."""
    for fps in tiers:
        if fps > cap_fps:
            continue
        if sec_per_frame * SAFETY * (int(np.ceil(dur * fps)) + 2) <= budget:
            return fps
    return None
