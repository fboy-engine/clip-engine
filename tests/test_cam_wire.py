import json
import os
import tempfile
import unittest

import numpy as np

from clip_engine import cam_enhance, cam_sr, render_clip

try:
    import cv2
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False


class LoadB3(unittest.TestCase):
    def write(self, d, obj):
        with open(os.path.join(d, "cam_bench.json"), "w") as f:
            json.dump(obj, f)

    def test_missing_error_old_and_lanczos_and_failed_gate_are_clear_errors(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "cam benchmark step must run"):
                render_clip.load_b3(d)
            self.write(d, {"error": "boom"})
            with self.assertRaisesRegex(ValueError, "benchmark failed.*boom"):
                render_clip.load_b3(d)
            self.write(d, {"selection": {}})
            with self.assertRaisesRegex(ValueError, "no b3 section"):
                render_clip.load_b3(d)
            self.write(d, {"b3": {"method": "lanczos", "rejected": {"realesrgan": "too slow"}}})
            with self.assertRaisesRegex(ValueError, "plain lanczos is not shipped.*too slow"):
                render_clip.load_b3(d)
            self.write(d, {"b3": {"method": "realesrgan", "ok": False, "blend": None, "reasons": ["edge strength 20 is not higher"]}})
            with self.assertRaisesRegex(ValueError, "quality gate failed.*edge"):
                render_clip.load_b3(d)

    def test_good_b3_is_returned(self):
        with tempfile.TemporaryDirectory() as d:
            b3 = {"method": "realesrgan", "ok": True, "blend": 0.7, "cam_fps": 15, "deblock_vf": "", "deblock": "none"}
            self.write(d, {"b3": b3})
            self.assertEqual(render_clip.load_b3(d)["blend"], 0.7)




class GateRule(unittest.TestCase):
    def test_strictly_lower_passes_and_equal_fails_when_old_is_neutral(self):
        self.assertTrue(cam_sr.blockiness_ok(0.98, 1.0)[0])
        self.assertFalse(cam_sr.blockiness_ok(1.0, 1.0)[0])

    def test_blurred_old_chain_below_one_does_not_demand_more_blur(self):
        ok, how = cam_sr.blockiness_ok(1.003, 0.911)           # the real numbers of bench run 45024407051
        self.assertTrue(ok)
        self.assertIn("no block grid", how)
        self.assertIn("blurs", how)

    def test_a_real_grid_still_fails_even_when_old_is_blurred(self):
        self.assertFalse(cam_sr.blockiness_ok(1.05, 0.911)[0])
        self.assertFalse(cam_sr.blockiness_ok(1.25, 1.2)[0])

    def test_gate_reports_which_rule_passed(self):
        base = {"blockiness": 0.911, "edge": 11.97, "fidelity": 0.993}
        g = cam_sr.gate_numbers({"fidelity": 0.981, "blockiness": 1.003, "edge": 14.03}, base)
        self.assertTrue(g["ok"])
        self.assertIn("no block grid", g["blockiness_rule"])

    def test_choose_blend_prefers_the_start_share_on_the_real_bench_numbers(self):
        rows = {1.0: {"fidelity": 0.9641, "blockiness": 1.0049, "edge": 15.7526},
                0.85: {"fidelity": 0.9719, "blockiness": 1.0048, "edge": 14.8544},
                0.7: {"fidelity": 0.9807, "blockiness": 1.0032, "edge": 14.0289},
                0.55: {"fidelity": 0.9866, "blockiness": 0.9991, "edge": 13.3589},
                0.4: {"fidelity": 0.9926, "blockiness": 0.9949, "edge": 12.9002},
                0.25: {"fidelity": 0.996, "blockiness": 0.9899, "edge": 12.5624}}
        base = {"fidelity": 0.9933, "blockiness": 0.911, "edge": 11.9748}
        self.assertEqual(cam_sr.choose_blend(rows, base, prefer=0.7)["blend"], 0.7)
        self.assertEqual(cam_sr.choose_blend(rows, base)["blend"], 1.0)              # default: the largest passing share
        rows[0.7] = dict(rows[0.7], fidelity=0.95)                                    # 0.7 drifts: next closest wins (tie -> smaller)
        self.assertEqual(cam_sr.choose_blend(rows, base, prefer=0.7)["blend"], 0.55)


class Report(unittest.TestCase):
    def test_probe_indices_are_spread_and_inside(self):
        idx = cam_enhance.probe_indices(2400)
        self.assertEqual(len(idx), cam_enhance.PROBE_FRAMES)
        self.assertTrue(all(0 <= i < 2400 for i in idx))
        self.assertEqual(cam_enhance.probe_indices(3), [0, 1, 2][:len(cam_enhance.probe_indices(3))])

    def test_quality_report_has_every_field_the_user_asked_for(self):
        class S:
            def info(self):
                return {"cam_fps": 15, "blend": 0.7, "interp": "flow", "roi": [1, 2, 3, 4], "sr_frames": 600, "sr_seconds": 410.5,
                        "flow_seconds": 30.0, "sec_per_sr_frame": 0.68}
        probe = {"rows": [{"fidelity": 0.98, "blockiness": 1.0, "edge": 14.0}, {"fidelity": 0.97, "blockiness": 1.01, "edge": 15.0}],
                 "old_rows": [{"fidelity": 0.99, "blockiness": 0.91, "edge": 12.0}] * 2, "pairs": []}
        info = {"deblock": "none", "ghost": {"ok": True, "detail": "fine"}, "flow_failed": None}
        q = cam_enhance.quality_report(S(), info, probe)
        for k in ("chain", "sr_fps", "sr_share", "sr_seconds", "fidelity", "blockiness", "edge", "ghost", "gate", "enhanced", "lanczos"):
            self.assertIn(k, q)
        self.assertEqual((q["sr_fps"], q["sr_share"], q["sr_seconds"]), (15, 0.7, 410.5))
        self.assertAlmostEqual(q["fidelity"], 0.975, 3)
        self.assertTrue(q["ghost_ok"])
        self.assertIn("70% SR", q["chain"])


class FakeUp:
    """Stands in for Real-ESRGAN: 4x lanczos plus a little sharpening."""
    name = "fake"

    def __init__(self):
        self.calls = 0

    def run4(self, img):
        self.calls += 1
        out = cv2.resize(img, None, fx=4, fy=4, interpolation=cv2.INTER_LANCZOS4)
        return cam_sr.sharpen(out, 0.5, 1.0)


class FakeProc:
    def __init__(self, data):
        import io
        self.stdout = io.BytesIO(data)

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


def moving_frames(n, w=120, h=70, speed=3.0, seed=0):
    """A textured disc moving to the right: enough structure for optical flow and for double-edge detection."""
    rng = np.random.default_rng(seed)
    tex = cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (0, 0), 1.5)
    out = []
    for i in range(n):
        f = np.full((h, w, 3), 70, np.uint8)
        cx = int(30 + i * speed)
        cv2.circle(f, (cx, h // 2), 18, (200, 180, 160), -1)
        m = np.zeros((h, w), np.uint8)
        cv2.circle(m, (cx, h // 2), 18, 255, -1)
        f[m > 0] = (0.6 * f[m > 0] + 0.4 * tex[m > 0]).astype(np.uint8)
        out.append(f)
    return out


@unittest.skipUnless(HAVE_CV2, "OpenCV not installed here: the pixel paths run in Actions")
class Stream(unittest.TestCase):
    def make(self, interp="flow", n_out=16, fps=8.0, cam_fps=4):
        roi = (0, 0, 120, 70)
        frames = moving_frames(int(n_out / fps * cam_fps) + 4)
        spawn = lambda cmd, **kw: FakeProc(b"".join(f.tobytes() for f in frames))
        boxes = [(10.0, 5.0, 100.0, 50.0)] * n_out
        plan = {"cam_fps": cam_fps, "blend": 0.7, "deblock_vf": "", "interp": interp}
        up = FakeUp()
        return cam_enhance.CamStream("x.mp4", 0.0, n_out / fps, fps, boxes, roi, plan, up, "8", spawn=spawn), up, frames

    def test_every_block_is_exactly_1080x540_in_flow_and_nearest_mode(self):
        for interp in ("flow", "nearest"):
            s, up, frames = self.make(interp)
            try:
                for i in range(16):
                    real = frames[min(len(frames) - 1, int(i / 8.0 * 4))]
                    blk = s.block(i, real)
                    self.assertEqual(blk.shape, (540, 1080, 3), interp)
                    self.assertEqual(blk.dtype, np.uint8)
            finally:
                s.close()

    def test_sr_runs_only_on_the_low_rate_frames(self):
        s, up, frames = self.make("flow", n_out=16, fps=8.0, cam_fps=4)
        for i in range(16):
            s.block(i, frames[0])
        s.close()
        self.assertLessEqual(up.calls, 16 / 8.0 * 4 + 3)           # about 8-9 SR frames, not 16
        self.assertEqual(s.info()["sr_frames"], up.calls)

    def test_flow_inbetweens_have_no_double_edges(self):
        """Ground truth at the OUTPUT rate; the stream only sees every second frame. An in-between block made by the
        optical-flow warp must match the true frame clearly better than a plain cross-fade of its two neighbours
        (the cross-fade is what ghosting looks like)."""
        from clip_engine import quality
        truth = moving_frames(24, speed=4.0)
        cam = truth[0::2]
        roi, box = (0, 0, 120, 70), (10.0, 5.0, 100.0, 50.0)
        spawn = lambda cmd, **kw: FakeProc(b"".join(f.tobytes() for f in cam))
        up = FakeUp()
        plan = {"cam_fps": 4, "blend": 0.7, "deblock_vf": "", "interp": "flow"}
        s = cam_enhance.CamStream("x.mp4", 0.0, 2.0, 8.0, [box] * 16, roi, plan, up, "8", spawn=spawn)
        flow_ssim, fade_ssim = [], []
        try:
            for i in range(14):
                blk = s.block(i, None, finish=False)
                if i % 2 == 1:
                    gt = cam_enhance.to_block(cv2.resize(truth[i], None, fx=4, fy=4, interpolation=cv2.INTER_LANCZOS4), box, roi, finish=False)
                    A = cam_enhance.to_block(up.run4(cam[i // 2]), box, roi, finish=False)
                    B = cam_enhance.to_block(up.run4(cam[i // 2 + 1]), box, roi, finish=False)
                    fade = cv2.addWeighted(A, 0.5, B, 0.5, 0)
                    flow_ssim.append(quality.ssim(gt, blk))
                    fade_ssim.append(quality.ssim(gt, fade))
        finally:
            s.close()
        self.assertGreater(np.mean(flow_ssim), np.mean(fade_ssim) + 0.01, (flow_ssim, fade_ssim))
        self.assertGreater(min(flow_ssim), 0.6)

    def test_probe_and_report_on_real_pixels(self):
        s, up, frames = self.make("flow")
        probe = cam_enhance.new_probe(16, 3)
        for i in range(16):
            real = frames[min(len(frames) - 1, int(i / 8.0 * 4))]
            blk = s.block(i, real)
            if i in probe["idx"]:
                cam_enhance.probe_frame(probe, i, blk, real, (10.0, 5.0, 100.0, 50.0), (0, 0))
        q = cam_enhance.quality_report(s, {"deblock": "none", "ghost": {"ok": True}}, probe)
        s.close()
        self.assertEqual(q["probe_frames"], 3)
        self.assertEqual(len(probe["pairs"]), 3)
        self.assertEqual(probe["pairs"][0][1].shape, (540, 1080, 3))
        self.assertGreater(q["fidelity"], 0.5)


if __name__ == "__main__":
    unittest.main()
