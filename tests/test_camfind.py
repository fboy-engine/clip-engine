"""A3-1: the generic cam/overlay detector on synthetic frames (fake face detector) and, when available, the real Twitch clip.
Stdlib unittest only (the CI unit-tests job has no pytest)."""
import os
import unittest

import numpy as np

try:
    import cv2
except ImportError:     # pragma: no cover
    raise unittest.SkipTest("opencv not installed")
from clip_engine import camfind as cf   # noqa: E402

W, H, N = 960, 540, 12


def frames(cam=(652, 0, 960, 212), border=True, chat=True, seed=1):
    """Gray frames: game noise that changes every frame, a static bordered cam, a scrolling chat column under it."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(N):
        g = cv2.resize(rng.integers(40, 200, (7, 12)).astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST)
        g = np.roll(g, (int(rng.integers(0, 77)), int(rng.integers(0, 80))), axis=(0, 1))     # game content moves: no fixed edges
        if cam:
            x0, y0, x1, y1 = cam
            g[y0:y1, x0:x1] = np.clip(45 + rng.normal(0, 3, (y1 - y0, x1 - x0)), 0, 255).astype(np.uint8)
            if border:                      # a 2 px border on every side that is not the frame edge
                if x0 > 0:
                    g[y0:y1, x0:x0 + 2] = 255
                if x1 < W:
                    g[y0:y1, x1 - 2:x1] = 255
                if y0 > 0:
                    g[y0:y0 + 2, x0:x1] = 255
                if y1 < H:
                    g[y1 - 2:y1, x0:x1] = 255
        if chat:
            top = cam[3] if cam else 0
            g[top:, 800:] = 30
            tr = np.random.default_rng(7)
            rows = [tr.integers(0, 2, 160 // 3 + 1).repeat(3)[:160] * 170 + 30 for _ in range(40)]
            scroll = (i * 9) % 26
            for r, row in enumerate(rows):
                y = top + r * 26 - scroll
                if top <= y and y + 12 <= H:
                    g[y:y + 12, 800:] = row.astype(np.uint8)
        out.append(g)
    return out


def faces_at(cx, cy, w, hit=1.0, jitter=1.5, seed=3):
    rng = np.random.default_rng(seed)

    def fn(gray):
        return [(i, cx + rng.normal(0, jitter), cy + rng.normal(0, jitter), float(w)) for i in range(len(gray))
                if rng.random() < hit]
    return fn


class Detect(unittest.TestCase):
    def test_top_right_cam_with_chat(self):
        r = cf.detect(frames(), face_fn=faces_at(735, 74, 42))
        x0, y0, x1, y1 = r["cam"]["rect_px"]
        self.assertTrue(abs(x0 - 652) <= 6 and y0 == 0 and x1 == W and abs(y1 - 212) <= 6)
        self.assertTrue(r["confidence"] >= 0.9 and r["cam"]["sides"]["left"] == "line" and r["cam"]["sides"]["bottom"] == "line")
        self.assertTrue(r["chat"] and r["chat"]["side"] == "right" and abs(r["chat"]["pitch_px"] - 26) <= 1)
        self.assertTrue("side_panel" in r["zones"] and r["zones"]["side_panel"][0] <= 652 + 6 and r["zones"]["side_panel"][3] == H)


    def test_bottom_left_cam_any_corner(self):
        r = cf.detect(frames(cam=(0, 330, 300, H), chat=False), face_fn=faces_at(120, 420, 40))
        x0, y0, x1, y1 = r["cam"]["rect_px"]
        self.assertTrue(x0 == 0 and y1 == H and abs(x1 - 300) <= 6 and abs(y0 - 330) <= 6 and r["confidence"] >= 0.6)
        self.assertTrue(r["chat"] is None and r["zones"].keys() == {"cam"})


    def test_no_face_fails_with_clear_message(self):
        with self.assertRaises(cf.CamNotConfident) as cm:
            cf.detect(frames(), face_fn=lambda g: [])
        e = cm.exception
        self.assertTrue("no stable face" in str(e) and "cam_rect" in str(e))


    def test_face_without_any_border_is_not_a_cam(self):
        with self.assertRaises(cf.CamNotConfident) as cm:
            cf.detect(frames(cam=None, chat=False), face_fn=faces_at(480, 270, 40))
        e = cm.exception
        self.assertTrue("cam_rect" in str(e))
        self.assertTrue(e.info["candidates"][0]["reasons"])


    def test_wandering_face_is_not_a_cam(self):     # a game character: seen in few frames, moving around
        def fn(gray):
            rng = np.random.default_rng(5)
            return [(i, float(rng.integers(50, 600)), float(rng.integers(50, 400)), 40.0) for i in range(len(gray)) if i % 3 == 0]
        with self.assertRaises(cf.CamNotConfident):
            cf.detect(frames(), face_fn=fn)


    def test_too_few_frames(self):
        with self.assertRaises(cf.CamNotConfident):
            cf.detect(frames()[:3], face_fn=faces_at(735, 74, 42))


    def test_two_equally_good_cams_are_ambiguous(self):
        fr = frames(cam=(652, 0, 960, 212), chat=False)
        for g in fr:                                    # a second bordered cam, bottom left
            g[330:540, 0:300] = 45
            g[330:540, 298:300] = 255
            g[330:332, 0:300] = 255

        def fn(gray):
            return [(i, 735, 74, 42.0) for i in range(len(gray))] + [(i, 120, 420, 42.0) for i in range(len(gray))]
        with self.assertRaises(cf.CamNotConfident) as cm:
            cf.detect(fr, face_fn=fn)
        e = cm.exception
        self.assertTrue("equally plausible" in str(e))


    def test_cam_window_is_two_to_one_and_never_stretched(self):
        w = cf.cam_window((1304, 0, 1920, 424), 1470, 148)           # the real Twitch cam
        self.assertTrue(w["box"][2] == 616 and w["box"][3] == 308 and w["mode"] == "upscale" and abs(w["scale"] - 1080 / 616) < 0.001)
        self.assertTrue(w["box"][0] == 1304 and 0 <= w["box"][1] <= 424 - 308)
        big = cf.cam_window((100, 100, 1500, 900), 800, 400)         # a cam wider than the 1080 output: downscale
        self.assertTrue(big["mode"] == "downscale" and big["scale"] < 1 and big["box"][2] == 1400 and big["box"][3] == 700)
        flat = cf.cam_window((0, 0, 1000, 300), 500, 100)            # height limits the 2:1 window
        self.assertTrue(flat["box"][2] == 600 and flat["box"][3] == 300)


    def test_override_helpers_do_not_invent_a_rect_when_not_confident(self):
        with self.assertRaises(cf.CamNotConfident):
            cf.detect(frames(), face_fn=faces_at(735, 74, 42, hit=0.2))


REAL = os.environ.get("CLIP_TWITCH_MP4")


class RealClip(unittest.TestCase):
    @unittest.skipUnless(REAL and os.path.exists(REAL), "set CLIP_TWITCH_MP4 to the Lauren Twitch clip")
    def test_real_twitch_clip(self):
        r = cf.detect_media(REAL, 0.5, 36.5, 1920, 1080)
        x0, y0, x1, y1 = r["cam"]["rect_px"]
        self.assertTrue(abs(x0 - 1304) <= 12 and y0 == 0 and x1 == 1920 and abs(y1 - 424) <= 12)
        self.assertTrue(r["confidence"] >= 0.9 and r["chat"]["side"] == "right")


# ---- the gate on the rendered output ------------------------------------------------------------

def _source():
    rng = np.random.default_rng(11)
    g = cv2.resize(rng.random((27, 48)).astype(np.float32), (1920, 1080), interpolation=cv2.INTER_CUBIC)
    g += 0.25 * cv2.resize(rng.random((108, 192)).astype(np.float32), (1920, 1080), interpolation=cv2.INTER_CUBIC)
    return (255 * (g - g.min()) / (g.max() - g.min())).astype(np.uint8)


def _output(src, panel_x, cam_box=(1304, 24, 616, 308), gain=0.8, bias=20):
    out = np.zeros((1920, 1080), np.uint8)
    x, y, w, h = cam_box
    out[:540] = cv2.resize(src[y:y + h, x:x + w], (1080, 540), interpolation=cv2.INTER_CUBIC)
    out[540:] = cv2.resize(src[0:1080, panel_x:panel_x + 845], (1080, 1380), interpolation=cv2.INTER_CUBIC)
    return np.clip(out.astype(np.float32) * gain + bias, 0, 255).astype(np.uint8)       # graded: the match must not care


ZONES = {"cam": (1304.0, 0.0, 1920.0, 424.0), "chat": (1728.0, 424.0, 1920.0, 1074.0), "side_panel": (1304.0, 0.0, 1920.0, 1080.0)}
PSC = list(np.arange(1.25, 1.46, 0.01))
CSC = [1.65, 1.70, 1.75, 1.80, 1.85]


class RenderedGate(unittest.TestCase):
    def test_gate_passes_a_clean_render_and_traces_both_blocks(self):
        s = _source()
        g = cf.gate_frames([(s, _output(s, 300))] * 2, ZONES, (1304, 0, 1920, 424), PSC, CSC)
        self.assertTrue(g["ok"], g["reasons"])
        self.assertTrue(abs(g["frames"][0]["panel"]["x_range"][0] - 300) <= 8 and g["frames"][0]["cam"]["inside_cam_rect"])


    def test_gate_fails_when_the_panel_shows_the_cam_column(self):
        s = _source()
        g = cf.gate_frames([(s, _output(s, 1000))], ZONES, (1304, 0, 1920, 424), PSC, CSC)      # panel covers x 1000-1845
        self.assertTrue(not g["ok"] and "overlaps" in " ".join(g["reasons"]) and "cam" in " ".join(g["reasons"]))


    def test_gate_fails_when_the_output_cannot_be_traced(self):
        s = _source()
        junk = np.random.default_rng(2).integers(0, 255, (1920, 1080)).astype(np.uint8)
        g = cf.gate_frames([(s, junk)], ZONES, (1304, 0, 1920, 424), PSC, CSC)
        self.assertTrue(not g["ok"] and "could not be traced" in " ".join(g["reasons"]))


    def test_gate_fails_when_the_cam_block_comes_from_the_game(self):
        s = _source()
        g = cf.gate_frames([(s, _output(s, 300, cam_box=(100, 500, 616, 308)))], ZONES, (1304, 0, 1920, 424), PSC, CSC)
        self.assertTrue(not g["ok"] and "outside the cam rectangle" in " ".join(g["reasons"]))


    def test_gate_with_no_frames_is_not_a_pass(self):
        self.assertTrue(not cf.gate_frames([], ZONES, None, PSC, CSC)["ok"])


if __name__ == "__main__":
    unittest.main()
