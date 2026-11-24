"""A3b: stable-overlay detector on synthetic frames (cam with NO drawn border, only a hard picture-change edge) and a fake face
detector. Expected rects are test expectations only (tolerance 3% of the frame); the code takes no position or size hints.
Stdlib unittest only."""
import unittest

import numpy as np

try:
    import cv2
except ImportError:     # pragma: no cover
    raise unittest.SkipTest("opencv not installed")
from clip_engine import camfind as cf          # noqa: E402
from clip_engine import stable_overlay as so   # noqa: E402

W, H, N = 960, 540, 16
TOL_X, TOL_Y = 0.03 * W, 0.03 * H


def frames(cam=(725, 0, 960, 227), game=True, seed=1, cam_level=45):
    """Gray frames: changing game picture everywhere; a pinned cam rectangle (no border line) whose content is nearly constant."""
    rng = np.random.default_rng(seed)
    base = cv2.resize(rng.integers(90, 200, (7, 12)).astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST)
    out = []
    for _ in range(N):
        if game:
            g = cv2.resize(rng.integers(90, 200, (7, 12)).astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST)
            g = np.roll(g, (int(rng.integers(0, 77)), int(rng.integers(0, 80))), axis=(0, 1))
        else:
            g = base.copy()
        if cam:
            x0, y0, x1, y1 = cam
            g[y0:y1, x0:x1] = np.clip(cam_level + rng.normal(0, 2, (y1 - y0, x1 - x0)), 0, 255).astype(np.uint8)
        out.append(g)
    return out


def face_at(cx, cy, w, hit=1.0, seed=3):
    rng = np.random.default_rng(seed)
    return lambda gray: [(i, cx + rng.normal(0, 1.5), cy + rng.normal(0, 1.5), float(w)) for i in range(len(gray)) if rng.random() < hit]


def near(rect, exp):
    return (abs(rect[0] - exp[0]) <= TOL_X and abs(rect[1] - exp[1]) <= TOL_Y and
            abs(rect[2] - exp[2]) <= TOL_X and abs(rect[3] - exp[3]) <= TOL_Y)


class StableOverlay(unittest.TestCase):
    def test_top_right_cam_without_border(self):
        r = so.detect(frames(), face_fn=face_at(840, 100, 60))
        self.assertTrue(near(r["cam"]["rect_px"], (725, 0, 960, 227)), r["cam"]["rect_px"])
        self.assertTrue(r["confidence"] >= cf.MIN_CONFIDENCE and r["method"] == "stable_overlay")
        self.assertTrue(r["face_hit_pct"] >= 90 and r["pinned"]["edge_persist"] >= 0.9 and r["pinned"]["outside_motion"] >= 2)
        self.assertTrue("cam" in r["zones"])

    def test_bigger_top_right_cam(self):                                    # a cam of another size is found the same way
        r = so.detect(frames(cam=(652, 0, 960, 212)), face_fn=face_at(800, 90, 60))
        self.assertTrue(near(r["cam"]["rect_px"], (652, 0, 960, 212)), r["cam"]["rect_px"])

    def test_bottom_left_cam(self):
        r = so.detect(frames(cam=(0, 330, 300, 540)), face_fn=face_at(150, 430, 50))
        self.assertTrue(near(r["cam"]["rect_px"], (0, 330, 300, 540)), r["cam"]["rect_px"])

    def test_bright_cam_on_darker_game(self):
        fr = frames(cam_level=235)
        r = so.detect(fr, face_fn=face_at(840, 100, 60))
        self.assertTrue(near(r["cam"]["rect_px"], (725, 0, 960, 227)), r["cam"]["rect_px"])

    def test_inner_edges_inside_the_cam_do_not_shrink_it(self):             # blanket / bed / shelf edges persist inside the cam picture
        fr = frames(cam=(725, 0, 960, 227), cam_level=60)
        for g in fr:
            g[60:200, 860:960] = 110        # a lighter inner panel with its own hard edges
            g[150:227, 725:800] = 20
        r = so.detect(fr, face_fn=face_at(840, 100, 60))
        self.assertTrue(near(r["cam"]["rect_px"], (725, 0, 960, 227)), r["cam"]["rect_px"])

    def test_cam_a_few_percent_from_the_frame_edge_snaps_to_it(self):
        fr = frames(cam=(725, 0, 940, 227), cam_level=60)
        for g in fr:
            g[:, 940:960] = 25              # thin dark strip at the frame edge (a shelf) that is part of the stream layout
        r = so.detect(fr, face_fn=face_at(830, 100, 60))
        self.assertTrue(r["cam"]["rect_px"][2] == W, r["cam"]["rect_px"])

    def test_still_scene_is_not_an_overlay(self):                           # nothing changes outside: cannot tell overlay from scene
        with self.assertRaises(cf.CamNotConfident) as cm:
            so.detect(frames(game=False), face_fn=face_at(840, 100, 60))
        self.assertTrue("barely changes" in str(cm.exception))

    def test_no_edge_no_cam(self):                                          # a face in pure game picture: no pinned edge
        with self.assertRaises(cf.CamNotConfident) as cm:
            so.detect(frames(cam=None), face_fn=face_at(480, 270, 60))
        self.assertTrue("cam_rect" in str(cm.exception))

    def test_no_face_no_cam(self):
        with self.assertRaises(cf.CamNotConfident) as cm:
            so.detect(frames(), face_fn=lambda g: [])
        self.assertTrue("no stable face" in str(cm.exception))

    def test_wandering_face_is_not_a_cam(self):
        def fn(gray):
            rng = np.random.default_rng(5)
            return [(i, float(rng.integers(50, 600)), float(rng.integers(50, 400)), 50.0) for i in range(len(gray)) if i % 3 == 0]
        with self.assertRaises(cf.CamNotConfident):
            so.detect(frames(), face_fn=fn)

    def test_too_few_frames(self):
        with self.assertRaises(cf.CamNotConfident):
            so.detect(frames()[:3], face_fn=face_at(840, 100, 60))

    def test_persistent_steps_ignore_moving_edges(self):
        vl, hl = so.persistent_steps(frames(cam=None))
        self.assertTrue(not vl and not hl)
        vl, hl = so.persistent_steps(frames())
        self.assertTrue(any(abs(l["x"] - 725) <= 4 for l in vl) and any(abs(l["y"] - 227) <= 4 for l in hl))


if __name__ == "__main__":
    unittest.main()
