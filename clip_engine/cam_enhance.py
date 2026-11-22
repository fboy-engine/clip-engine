"""B3: the cam block chain. The block is ALWAYS exactly 1080x540 from the source cam crop (never shrunk).

  ffmpeg prepass at cam_fps: full-frame deblock (only if measured to help) -> crop to the ROI -> light hqdn3d
  -> super-resolution on the ROI only (4x) -> blend with the lanczos upscale (the face stays the real face)
  -> in-between output frames by motion compensation (optical flow, forward-backward checked) -> sub-pixel crop
  to exactly 1080x540 -> gentle sharpen + very light grain.

No face-restoration models. The SR stage runs on a few frames per second only; the frames in between are warped
from the two nearest SR frames (or, as a fallback, the nearest SR frame plus a light lanczos detail term).
"""
import math
import subprocess
import time

import numpy as np

from . import cam_sr, layout, quality

OUT_W, CAM_H = 1080, layout.CAM_H
S4 = cam_sr.SR_FACTOR
GHOST_TEST_SEC = 1.5
CAM_FPS_CAP = 20              # user decision: 15 fps, 20 if the time allows
PROBE_FRAMES = 6              # output frames of the real render that are measured against the real crop and the old chain
NEAREST_DETAIL = 0.3          # share of the real-frame detail added to the nearest SR frame in the fallback mode
OLD_SHARPEN = (0.35, 1.2)     # the previous (lanczos) chain's unsharp, kept so "old vs new" is the real old chain


# ---- geometry (pure) --------------------------------------------------------------------------------------

def roi_union(boxes, region, margin=cam_sr.ROI_MARGIN, src_wh=None):
    """Integer ROI (x, y, w, h) holding every per-frame cam box plus a margin, inside the cam region (and the frame)."""
    rx0, ry0, rx1, ry1 = region
    if src_wh:
        rx1, ry1 = min(rx1, src_wh[0]), min(ry1, src_wh[1])
    x0 = max(rx0, int(math.floor(min(b[0] for b in boxes))) - margin)
    y0 = max(ry0, int(math.floor(min(b[1] for b in boxes))) - margin)
    x1 = min(rx1, int(math.ceil(max(b[0] + b[2] for b in boxes))) + margin)
    y1 = min(ry1, int(math.ceil(max(b[1] + b[3] for b in boxes))) + margin)
    return x0, y0, x1 - x0, y1 - y0


def block_matrix(left, top, w, h, ox, oy, scale=S4, out_w=OUT_W, out_h=CAM_H):
    """cv2.warpAffine matrix for a sub-pixel crop: the source picture is the source region whose top-left is source
    pixel (ox, oy), stored at `scale` x; the output is out_w x out_h showing exactly the source box (left, top, w, h)
    (float, any position). Pixel edges, not centres, are aligned, so no half-pixel drift at any scale."""
    kx, ky = out_w / (w * scale), out_h / (h * scale)
    sx0, sy0 = (left - ox) * scale, (top - oy) * scale
    return np.float32([[kx, 0, -0.5 + kx * (0.5 - sx0)], [0, ky, -0.5 + ky * (0.5 - sy0)]])


def window_4x(box, roi, shape4, pad=3):
    """Integer window (x0, y0, x1, y1) of the 4x ROI picture that covers the box (+pad), and the source-pixel
    position of its top-left (for block_matrix)."""
    left, top, w, h = box
    sx0, sy0 = (left - roi[0]) * S4, (top - roi[1]) * S4
    x0, y0 = max(0, int(math.floor(sx0)) - pad), max(0, int(math.floor(sy0)) - pad)
    x1 = min(shape4[1], int(math.ceil(sx0 + w * S4)) + pad)
    y1 = min(shape4[0], int(math.ceil(sy0 + h * S4)) + pad)
    return (x0, y0, x1, y1), (roi[0] + x0 / S4, roi[1] + y0 / S4)


def prepass_cmd(media, start, dur, cam_fps, deblock_vf, roi, denoise=cam_sr.DENOISE_VF):
    x, y, w, h = roi
    vf = ",".join(s for s in (f"fps={cam_fps}", deblock_vf, f"crop={w}:{h}:{x}:{y}", denoise) if s)
    return ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", media, "-an", "-vf", vf,
            "-sws_flags", "lanczos+accurate_rnd+full_chroma_int", "-frames:v", str(int(math.ceil(dur * cam_fps)) + 2),
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]


def real_roi_cmd(media, start, dur, fps_arg, roi, n):
    """The untouched real ROI frames at the OUTPUT rate (reference for the ghost test)."""
    x, y, w, h = roi
    return ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", media, "-an",
            "-vf", f"fps={fps_arg},crop={w}:{h}:{x}:{y}", "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]


# ---- the old chain (what the render shipped before B3) and the metrics ------------------------------------

def old_block(roi_img, box, roi_xy):
    """Plain-lanczos chain of the previous render: light bilateral denoise, lanczos4 to 1080x540, unsharp 0.35/1.2.
    roi_img: untouched BGR pixels whose top-left is source pixel roi_xy."""
    import cv2
    den = cv2.bilateralFilter(roi_img, 5, 20, 5)
    m = block_matrix(box[0], box[1], box[2], box[3], roi_xy[0], roi_xy[1], scale=1)
    cam = cv2.warpAffine(den, m, (OUT_W, CAM_H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)
    return cam_sr.sharpen(cam, *OLD_SHARPEN)


def real_small(roi_img, box, roi_xy):
    """The real (untouched) box as a small picture of round(w) x round(h) px: the reference for the fidelity."""
    import cv2
    w, h = max(1, int(round(box[2]))), max(1, int(round(box[3])))
    m = block_matrix(box[0], box[1], box[2], box[3], roi_xy[0], roi_xy[1], scale=1, out_w=w, out_h=h)
    return cv2.warpAffine(roi_img, m, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE)


def block_metrics(block, ref_small, box):
    """fidelity = SSIM of the block shrunk back to the real crop's size against the untouched real crop; blockiness
    on the source 8 px grid as it lands in the block; edge strength (95th percentile gradient)."""
    import cv2
    h, w = ref_small.shape[:2]
    back = cv2.resize(block, (w, h), interpolation=cv2.INTER_AREA)
    s = OUT_W / box[2]
    grid = max(2, int(round(8 * s)))
    ox, oy = int(round(((-box[0]) % 8) * s)), int(round(((-box[1]) % 8) * s))
    return {"fidelity": quality.ssim(ref_small, back), "blockiness": quality.blockiness(block, grid, ox, oy),
            "edge": quality.edge_strength(block)}


def mean_rows(rows):
    return {k: round(float(np.mean([r[k] for r in rows])), 4) for k in rows[0]}


def sr_mix(up, clean_roi, blend):
    """SR picture of the ROI at 4x mixed with the lanczos 4x (blend = SR share), and the lanczos 4x itself."""
    import cv2
    lan = cv2.resize(clean_roi, None, fx=S4, fy=S4, interpolation=cv2.INTER_LANCZOS4)
    sr = up.run4(clean_roi)
    if sr.shape != lan.shape:
        raise RuntimeError(f"upscaler output {sr.shape[:2]} != expected {lan.shape[:2]}")
    mixed = sr if blend >= 0.999 else cv2.addWeighted(sr, blend, lan, 1.0 - blend, 0)
    return mixed, lan, sr


def to_block(img4, box, roi, seed=0, finish=True):
    """Sub-pixel crop of the 4x ROI picture to exactly 1080x540 (+ gentle sharpen + very light grain)."""
    import cv2
    (x0, y0, x1, y1), org = window_4x(box, roi, img4.shape)
    m = block_matrix(box[0], box[1], box[2], box[3], org[0], org[1], scale=S4)
    out = cv2.warpAffine(np.ascontiguousarray(img4[y0:y1, x0:x1]), m, (OUT_W, CAM_H), flags=cv2.INTER_LANCZOS4,
                         borderMode=cv2.BORDER_REPLICATE)
    return cam_sr.finish(out, seed) if finish else out


# ---- the streaming chain ----------------------------------------------------------------------------------

class CamStream:
    """Sequential producer of the 1080x540 cam block for output frames 0..n-1.

    plan = {"cam_fps", "blend", "deblock_vf", "interp": "flow"|"nearest"}. `block(i, full_frame)` must be called with
    i = 0, 1, 2, ... (the render loop does). The prepass ffmpeg is started lazily and killed by close()."""

    def __init__(self, media, start, dur, fps, boxes, roi, plan, up, fps_arg, log=print, spawn=subprocess.Popen):
        self.media, self.start, self.dur, self.fps = media, start, dur, fps
        self.boxes, self.roi, self.plan, self.up = boxes, tuple(roi), plan, up
        self.cam_fps, self.blend = plan["cam_fps"], plan["blend"]
        self.interp = plan.get("interp", "flow")
        self.fps_arg, self.log, self.spawn = fps_arg, log, spawn
        self.proc = None
        self.clean, self.mixed, self.lan4 = {}, {}, {}
        self.read_idx = -1
        self.pair = None
        self.sr_sec, self.sr_frames, self.flow_sec = 0.0, 0, 0.0
        self.fsize = self.roi[2] * self.roi[3] * 3

    # -- prepass reading
    def _open(self):
        self.proc = self.spawn(prepass_cmd(self.media, self.start, self.dur, self.cam_fps, self.plan.get("deblock_vf", ""), self.roi),
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def _read_to(self, k):
        if self.proc is None:
            self._open()
        while self.read_idx < k:
            buf = self.proc.stdout.read(self.fsize)
            if len(buf) == self.fsize:
                img = np.frombuffer(buf, np.uint8).reshape(self.roi[3], self.roi[2], 3).copy()
            elif self.read_idx >= 0:
                img = self.clean[self.read_idx]            # past the end: hold the last frame
            else:
                raise RuntimeError("cam prepass produced no frames")
            self.read_idx += 1
            self.clean[self.read_idx] = img
        for old in [j for j in self.clean if j < k - 1]:
            self.clean.pop(old, None)
            self.mixed.pop(old, None)
            self.lan4.pop(old, None)

    def _sr(self, k):
        if k in self.mixed:
            return
        self._read_to(k)
        t = time.perf_counter()
        self.mixed[k], self.lan4[k], _ = sr_mix(self.up, self.clean[k], self.blend)
        self.sr_sec += time.perf_counter() - t
        self.sr_frames += 1

    # -- optical flow between two prepass frames (computed once per pair)
    def _flow_pair(self, k):
        import cv2
        if self.pair is not None and self.pair["k"] == k:
            return self.pair
        t = time.perf_counter()
        gA = cv2.cvtColor(self.clean[k], cv2.COLOR_BGR2GRAY)
        gB = cv2.cvtColor(self.clean[k + 1], cv2.COLOR_BGR2GRAY)
        dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        fAB, fBA = dis.calc(gA, gB, None), dis.calc(gB, gA, None)
        h, w = gA.shape
        gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        bx = cv2.remap(fBA[..., 0], gx + fAB[..., 0], gy + fAB[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        by = cv2.remap(fBA[..., 1], gx + fAB[..., 0], gy + fAB[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        err = np.hypot(fAB[..., 0] + bx, fAB[..., 1] + by)                  # forward-backward inconsistency, px
        rel = cv2.GaussianBlur(np.clip(1.0 - (err - 0.5), 0.0, 1.0).astype(np.float32), (0, 0), 2.0)
        H4, W4 = h * S4, w * S4
        up2 = lambda f: cv2.resize(f, (W4, H4), interpolation=cv2.INTER_LINEAR) * S4
        if self.pair is None or "grid" not in self.pair:
            grid = np.meshgrid(np.arange(W4, dtype=np.float32), np.arange(H4, dtype=np.float32))
        else:
            grid = self.pair["grid"]
        self.pair = {"k": k, "fAB": up2(fAB), "fBA": up2(fBA), "rel": cv2.resize(rel, (W4, H4)), "grid": grid,
                     "mean_flow": float(np.hypot(fAB[..., 0], fAB[..., 1]).mean())}
        self.flow_sec += time.perf_counter() - t
        return self.pair

    def _inbetween(self, i, k, a, box, real_roi):
        """4x window picture of the output instant between SR frames k and k+1 (a in (0, 1))."""
        import cv2
        A, B = self.mixed[k], self.mixed[k + 1]
        win, _ = window_4x(box, self.roi, A.shape)
        x0, y0, x1, y1 = win
        if self.interp == "nearest":
            near, lan_n = (A, self.lan4[k]) if a < 0.5 else (B, self.lan4[k + 1])
            lan_t = cv2.resize(real_roi, None, fx=S4, fy=S4, interpolation=cv2.INTER_LANCZOS4)
            d = lan_t[y0:y1, x0:x1].astype(np.float32) - lan_n[y0:y1, x0:x1].astype(np.float32)
            out = near[y0:y1, x0:x1].astype(np.float32) + NEAREST_DETAIL * d
            full = np.zeros_like(A)
            full[y0:y1, x0:x1] = np.clip(np.rint(out), 0, 255).astype(np.uint8)
            return full
        pr = self._flow_pair(k)
        gx, gy = pr["grid"][0][y0:y1, x0:x1], pr["grid"][1][y0:y1, x0:x1]
        fa, fb = pr["fAB"][y0:y1, x0:x1], pr["fBA"][y0:y1, x0:x1]
        Aw = cv2.remap(A, gx - a * fa[..., 0], gy - a * fa[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        Bw = cv2.remap(B, gx - (1 - a) * fb[..., 0], gy - (1 - a) * fb[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        rel = pr["rel"][y0:y1, x0:x1][..., None]
        wb = rel * a + (1.0 - rel) * (1.0 if a >= 0.5 else 0.0)             # unreliable pixels: take the nearest frame
        out = Aw.astype(np.float32) * (1.0 - wb) + Bw.astype(np.float32) * wb
        full = np.zeros_like(A)
        full[y0:y1, x0:x1] = np.clip(np.rint(out), 0, 255).astype(np.uint8)
        return full

    def block(self, i, real_roi=None, finish=True):
        """The finished 1080x540 block for output frame i. real_roi: the untouched real ROI pixels of this frame
        (needed only by the 'nearest' mode)."""
        pos = (i / self.fps) * self.cam_fps
        k = int(math.floor(pos + 1e-6))
        a = pos - k
        box = self.boxes[min(i, len(self.boxes) - 1)]
        self._sr(k)
        if a < 0.02:
            img4 = self.mixed[k]
        else:
            self._sr(k + 1)
            if a > 0.98:
                img4 = self.mixed[k + 1]
            else:
                if self.interp == "nearest" and real_roi is None:
                    raise ValueError("nearest mode needs the real ROI frame")
                img4 = self._inbetween(i, k, a, box, real_roi)
        return to_block(img4, box, self.roi, seed=i, finish=finish)

    def info(self):
        return {"cam_fps": self.cam_fps, "blend": self.blend, "interp": self.interp, "roi": list(self.roi),
                "deblock_vf": self.plan.get("deblock_vf", ""), "sr_frames": self.sr_frames,
                "sr_seconds": round(self.sr_sec, 1), "flow_seconds": round(self.flow_sec, 1),
                "sec_per_sr_frame": round(self.sr_sec / max(1, self.sr_frames), 3)}

    def close(self):
        if self.proc is not None:
            try:
                self.proc.stdout.close()
                self.proc.terminate()
                self.proc.wait(timeout=10)
            except Exception:
                pass
            self.proc = None


def read_frames(cmd, w, h, spawn=subprocess.Popen):
    """All frames of a small rawvideo bgr24 command as a list (for the short ghost test only)."""
    p = spawn(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size, out = w * h * 3, []
    while True:
        buf = p.stdout.read(size)
        if len(buf) != size:
            break
        out.append(np.frombuffer(buf, np.uint8).reshape(h, w, 3).copy())
    p.stdout.close()
    p.wait()
    return out


def ghost_test(media, start, dur, fps, fps_arg, boxes, roi, plan, up, log=print, spawn=subprocess.Popen):
    """Short run of the real chain on the most moving 1.5 s of the clip, compared with the REAL frames of the same
    instants. Returns {"ok", "detail", ...}: in-between frames must match the real face as well as the keyframes do."""
    import cv2
    n_all = len(boxes)
    n_test = int(GHOST_TEST_SEC * fps)
    if n_all < n_test + 2:
        return {"ok": True, "detail": "clip too short to test", "skipped": True}
    # find the most moving second and a half: coarse scan of the real ROI at 8 fps
    scan = read_frames(real_roi_cmd(media, start, dur, "8", roi, int(dur * 8) + 2), roi[2], roi[3], spawn)
    mot = [float(np.abs(scan[j].astype(np.int16) - scan[j - 1].astype(np.int16)).mean()) for j in range(1, len(scan))]
    wlen = max(1, int(GHOST_TEST_SEC * 8))
    best_j = int(np.argmax([sum(mot[j:j + wlen]) for j in range(0, max(1, len(mot) - wlen))])) if mot else 0
    t0 = min(best_j / 8.0, max(0.0, dur - GHOST_TEST_SEC - 0.2))
    i0 = int(round(t0 * fps))
    real = read_frames(real_roi_cmd(media, start + i0 / fps, GHOST_TEST_SEC, fps_arg, roi, n_test), roi[2], roi[3], spawn)
    n_test = min(n_test, len(real))
    sub_boxes = boxes[i0:i0 + n_test]
    stream = CamStream(media, start + i0 / fps, n_test / fps, fps, sub_boxes, roi, plan, up, fps_arg, log, spawn)
    key, inter = [], []
    try:
        for j in range(n_test):
            blk = stream.block(j, real[j], finish=False)
            ref = real_small(real[j], sub_boxes[j], roi[:2])
            h, w = ref.shape[:2]
            fid = quality.ssim(ref, cv2.resize(blk, (w, h), interpolation=cv2.INTER_AREA))
            pos = (j / fps) * plan["cam_fps"]
            frac = pos - math.floor(pos + 1e-6)
            (key if (frac < 0.02 or frac > 0.98) else inter).append(fid)
    finally:
        stream.close()
    ok, detail = cam_sr.ghost_verdict(key, inter)
    return {"ok": bool(ok), "detail": detail, "interp": plan.get("interp", "flow"), "window_start": round(i0 / fps, 2),
            "keyframes": len(key), "inbetween": len(inter), "motion": round(float(np.mean(mot)) if mot else 0.0, 3),
            "sr_seconds": round(stream.sr_sec, 1)}


# ---- planning, probing, reporting (the render calls these) -----------------------------------------------------

def grab_roi(media, t, roi, prefix="", suffix="", run=subprocess.run):
    """One real ROI frame at time t (filters before the crop = on the full frame, after = on the ROI), BGR or None."""
    x, y, w, h = roi
    vf = ",".join(s for s in (prefix, f"crop={w}:{h}:{x}:{y}", suffix) if s)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", media, "-frames:v", "1", "-vf", vf,
           "-sws_flags", "lanczos+accurate_rnd+full_chroma_int", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = run(cmd, capture_output=True)
    if len(p.stdout) != w * h * 3:
        return None
    return np.frombuffer(p.stdout, np.uint8).reshape(h, w, 3).copy()


def time_sr(up, img, n=2):
    """Median seconds per 4x call on a production-size ROI, after one warm-up call (model load, memory)."""
    up.run4(img)
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        up.run4(img)
        ts.append(time.perf_counter() - t)
    return float(np.median(ts))


def prepare(media, start, dur, fps, fps_arg, boxes, region, src_wh, b3, up, log=print, spawn=subprocess.Popen,
            grab=grab_roi, cap_fps=CAM_FPS_CAP):
    """Everything before the first frame: the ROI, the SR rate for THIS clip (highest of 20/15/12/10/8 that fits
    the time budget on the measured s/frame), the in-between mode (optical flow; if the ghost test sees double
    edges: nearest SR frame + real detail; if that ghosts too the render refuses) and the CamStream.
    b3 = the benchmark's chain (cam_bench.json): SR share and deblock filter. Returns (stream, info)."""
    roi = roi_union(boxes, region, src_wh=src_wh)
    deb = b3.get("deblock_vf", "")
    img = grab(media, start + dur / 2, roi, deb, cam_sr.DENOISE_VF)
    if img is None:
        raise ValueError("cam enhance: could not cut the cam ROI out of the source")
    sec = time_sr(up, img)
    cam_fps = cam_sr.plan_cam_fps(sec, dur, cap_fps)
    log(f"cam SR: ROI {roi[2]}x{roi[3]} px, {sec:.2f} s per frame -> {cam_fps} fps for a {dur:.1f} s clip")
    if cam_fps is None:
        raise ValueError(f"cam enhance: super-resolution takes {sec:.2f} s per frame, too slow for a {dur:.1f} s clip "
                         f"even at {cam_sr.B3_FPS_TIERS[-1]} fps (budget {cam_sr.SR_BUDGET_SEC} s): refusing the clip, "
                         f"plain lanczos is not shipped")
    plan = {"cam_fps": cam_fps, "blend": b3["blend"], "deblock_vf": deb, "interp": "flow"}
    ghost = ghost_test(media, start, dur, fps, fps_arg, boxes, roi, plan, up, log, spawn)
    log(f"ghost test (optical flow): {'ok' if ghost['ok'] else 'FAILED'} - {ghost['detail']}")
    flow_failed = None
    if not ghost["ok"]:
        flow_failed = ghost["detail"]
        plan["interp"] = "nearest"
        ghost = ghost_test(media, start, dur, fps, fps_arg, boxes, roi, plan, up, log, spawn)
        log(f"ghost test (nearest SR frame + real detail): {'ok' if ghost['ok'] else 'FAILED'} - {ghost['detail']}")
        if not ghost["ok"]:
            raise ValueError("cam enhance: in-between frames show double edges with both the optical-flow and the "
                             f"nearest-frame mode (flow: {flow_failed}; nearest: {ghost['detail']}): refusing the clip")
    stream = CamStream(media, start, dur, fps, boxes, roi, plan, up, fps_arg, log, spawn)
    return stream, {"roi": list(roi), "sec_per_frame": round(sec, 3), "plan": dict(plan), "ghost": ghost,
                    "flow_failed": flow_failed, "deblock": b3.get("deblock", ""), "b3_cam_fps": b3.get("cam_fps")}


def probe_indices(n, k=PROBE_FRAMES):
    return sorted({min(n - 1, int((j + 0.5) / k * n)) for j in range(k)})


def new_probe(n, k=PROBE_FRAMES):
    return {"idx": set(probe_indices(n, k)), "rows": [], "old_rows": [], "pairs": []}


def probe_frame(probe, i, block, real_roi, box, roi_xy, keep=3):
    """Measure one finished output block against the untouched real crop, and the OLD chain on the same frame."""
    real = np.ascontiguousarray(real_roi)
    ref = real_small(real, box, roi_xy)
    old = old_block(real, box, roi_xy)
    probe["rows"].append(block_metrics(block, ref, box))
    probe["old_rows"].append(block_metrics(old, ref, box))
    if len(probe["pairs"]) < keep:
        probe["pairs"].append((i, old, block.copy()))


def quality_report(stream, info, probe):
    """cam_quality for render.json: measured on the frames the render really produced."""
    enh, old = mean_rows(probe["rows"]), mean_rows(probe["old_rows"])
    s = stream.info()
    gate = cam_sr.gate_numbers(enh, old)
    return {"method": "realesrgan", "chain": (f"deblock {info.get('deblock') or 'none'} (full frame) + hqdn3d + Real-ESRGAN x4 on the ROI at "
                                              f"{s['cam_fps']} fps, {s['interp']} in-betweens, {int(round(s['blend'] * 100))}% SR / "
                                              f"{100 - int(round(s['blend'] * 100))}% lanczos, sub-pixel crop to 1080x540, unsharp "
                                              f"{cam_sr.UNSHARP_AMOUNT} + grain {cam_sr.GRAIN_SIGMA}"),
            "cam_fps": s["cam_fps"], "sr_fps": s["cam_fps"], "blend": s["blend"], "sr_share": s["blend"], "interp": s["interp"],
            "deblock": info.get("deblock") or "none", "roi": s["roi"], "sr_frames": s["sr_frames"],
            "sr_seconds": s["sr_seconds"], "flow_seconds": s["flow_seconds"], "sec_per_sr_frame": s["sec_per_sr_frame"],
            "enhanced": enh, "lanczos": old, "fidelity": enh["fidelity"], "blockiness": enh["blockiness"], "edge": enh["edge"],
            "ghost": info.get("ghost"), "ghost_ok": bool((info.get("ghost") or {}).get("ok")),
            "flow_failed": info.get("flow_failed"), "gate": gate, "probe_frames": len(probe["rows"])}
