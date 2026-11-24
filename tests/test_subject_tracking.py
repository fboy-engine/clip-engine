import unittest

import numpy as np

from clip_engine import render_clip, subject, vision

H, W, N = 135, 240, 14


def scene(blob_cx=None, noise_zone=None, global_noise=False, seed=0):
    """Gray stack: a fixed textured block at the left (an overlay figure), an optional moving blob, optional noise zone."""
    rng = np.random.RandomState(seed)
    g = np.full((N, H, W), 90.0, np.float32)
    tex = rng.rand(60, 28) * 80 + 60
    g[:, 40:100, 14:42] = tex                                  # static: same pixels in every sample
    for i in range(N):
        if blob_cx is not None:
            cx = int(blob_cx(i) * W)
            g[i, 40:100, max(0, cx - 14):cx + 14] = rng.rand(60, min(28, cx + 14 - max(0, cx - 14))) * 200 + 20
        if noise_zone is not None:
            x0, x1 = noise_zone
            g[i, :, x0:x1] = rng.rand(H, x1 - x0) * 255
        if global_noise:
            g[i] += rng.rand(H, W) * 120
    return g


STATIC_BOX = (14 / W, 40 / H, 42 / W, 100 / H, 0.5)
MASKS = [(0.68, 0.0, 1.0, 1.0)]                                # side panel to the right of x = 0.68


class DropStatic(unittest.TestCase):
    def test_static_cluster_is_dropped_and_a_moving_one_is_kept(self):
        g = scene(blob_cx=lambda i: 0.30 + 0.01 * i)
        play = subject.playable_mask(H, W, MASKS)
        moving = [(0.30 + 0.01 * i - 14 / W, 40 / H, 0.30 + 0.01 * i + 14 / W, 100 / H, 0.9) for i in range(N)]
        dets = [[STATIC_BOX, moving[i]] for i in range(N)]
        out, info = subject.drop_static(dets, g, play)
        self.assertTrue(all(len(d) == 1 for d in out))
        self.assertAlmostEqual(out[0][0][0], moving[0][0], places=6)
        static = [c for c in info if c["static"]]
        self.assertEqual(len(static), 1)
        self.assertLess(static[0]["cx"], 0.2)

    def test_screen_fixed_player_with_a_moving_world_behind_is_kept(self):
        rng = np.random.RandomState(3)
        g = rng.rand(N, H, W).astype(np.float32) * 120          # busy world: every pixel changes
        g[:, 30:110, 100:140] = rng.rand(N, 80, 40).astype(np.float32) * 45   # a follow-cam player: fixed place, about a third of the motion
        box = (100 / W, 30 / H, 140 / W, 110 / H, 0.8)
        out, info = subject.drop_static([[box] for _ in range(N)], g, subject.playable_mask(H, W, []))
        self.assertTrue(all(len(d) == 1 for d in out))
        self.assertFalse(any(c["static"] for c in info))

    def test_too_few_hits_are_never_called_static(self):
        g = scene()
        play = subject.playable_mask(H, W, MASKS)
        dets = [[STATIC_BOX] if i < subject.STATIC_MIN_HITS - 1 else [] for i in range(N)]
        out, info = subject.drop_static(dets, g, play)
        self.assertEqual(sum(len(d) for d in out), subject.STATIC_MIN_HITS - 1)


class BuildTracked(unittest.TestCase):
    def test_crop_follows_the_moving_mass_not_the_static_figure(self):
        g = scene(blob_cx=lambda i: 0.35 + 0.01 * i)
        dets = [[STATIC_BOX] for _ in range(N)]
        sub = subject.build_tracked(dets, g, MASKS)
        self.assertEqual(set(sub["source"]), {"motion"})
        self.assertTrue(all(sub["tracked"]))
        for i in range(1, N - 1):
            self.assertAlmostEqual(sub["cx"][i], 0.35 + 0.01 * i, delta=0.06)
        self.assertEqual(len([c for c in sub["static"] if c["static"]]), 1)

    def test_motion_in_a_masked_zone_is_ignored(self):
        g = scene(noise_zone=(180, 240))                       # chat-like noise inside the masked side panel
        sub = subject.build_tracked([[] for _ in range(N)], g, MASKS)
        self.assertEqual(set(sub["source"]), {"none"})
        self.assertFalse(any(sub["tracked"]))

    def test_diffuse_motion_is_not_a_subject(self):
        g = scene(global_noise=True)
        sub = subject.build_tracked([[] for _ in range(N)], g, [])
        self.assertEqual(set(sub["source"]), {"none"})

    def test_a_live_person_wins_over_motion(self):
        g = scene(blob_cx=lambda i: 0.35)
        person = (0.30, 0.2, 0.40, 0.8, 1.2)
        sub = subject.build_tracked([[person] for _ in range(N)], g, MASKS)
        self.assertEqual(set(sub["source"]), {"person"})
        self.assertAlmostEqual(sub["cx"][3], 0.35, places=6)


class Stats(unittest.TestCase):
    def test_in_panel_pct_counts_every_sample(self):
        boxes = [(0.1, 0.0, 0.2, 1.0, 0.0)] * 3 + [None] + [(0.8, 0.0, 0.9, 1.0, 0.0)]
        st = subject.inside_stats(boxes, [0, 1, 2, 3, 4], [0] * 5, [960] * 5, 1920)
        self.assertEqual(st["in_panel_pct"], 60.0)              # 3 of 5 samples inside; one untracked, one cut
        self.assertEqual(st["tracked_pct"], 80.0)
        self.assertEqual(st["inside_pct"], 75.0)

    def test_report_and_gate_constant(self):
        g = scene(blob_cx=lambda i: 0.35)
        sub = subject.build_tracked([[STATIC_BOX] for _ in range(N)], g, MASKS)
        plan = subject.inside_stats(sub["box"], list(range(N)), [0.0] * N, [1920 * 0.44] * N, 1920)
        rep = render_clip.tracking_report(sub, plan, list(range(N)), 2)
        self.assertEqual(rep["gate_pct"], 85.0)
        self.assertEqual(len(rep["per_sample"]), N)
        self.assertEqual(rep["sources_pct"]["motion"], 100.0)
        self.assertEqual(len(rep["static_dropped"]), 1)


class Masks(unittest.TestCase):
    def test_mask_list_accepts_one_rect_or_many(self):
        self.assertEqual(vision.mask_list(None), [])
        self.assertEqual(vision.mask_list((0.1, 0.2, 0.3, 0.4)), [(0.1, 0.2, 0.3, 0.4)])
        self.assertEqual(len(vision.mask_list([(0, 0, 1, 1), (0.5, 0.5, 1, 1)])), 2)

    def test_tracking_masks_cover_cam_and_zones(self):
        m = render_clip.tracking_masks((0.68, 0.0, 1.0, 0.39), {"chat": (1696, 424, 1920, 1075)}, 1920, 1080)
        self.assertEqual(len(m), 2)
        self.assertAlmostEqual(m[1][0], 1696 / 1920)
        self.assertEqual(render_clip.tracking_masks(None, None, 1920, 1080), [])


if __name__ == "__main__":
    unittest.main()


class TrackSubject(unittest.TestCase):
    def box(self, cx, cy=0.5, w=0.1, h=0.3, s=0.8, label="person"):
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, s, label)

    def test_central_persistent_track_beats_a_brief_edge_one(self):
        dets = [[self.box(0.5 + 0.01 * i)] + ([self.box(0.95)] if i in (3, 4) else []) for i in range(10)]
        sub = subject.track_subject(dets)
        self.assertTrue(all(sub["tracked"]))
        self.assertTrue(all(0.45 < c < 0.65 for c in sub["cx"]))

    def test_sticky_no_flip_on_a_slightly_better_challenger(self):
        dets = [[self.box(0.45, s=0.6), self.box(0.62, s=0.62)] for _ in range(10)]
        sub = subject.track_subject(dets)
        self.assertEqual(len(set(sub["track"])), 1)

    def test_brief_loss_is_held_then_given_up(self):
        dets = [[self.box(0.5)] for _ in range(4)] + [[] for _ in range(6)]
        sub = subject.track_subject(dets, hold=2)
        self.assertEqual(sub["tracked"], [True] * 6 + [False] * 4)
        self.assertEqual(sub["held"][4:6], [True, True])
        self.assertTrue(all(b is None for b in sub["box"][6:]))

    def test_loss_before_any_detection_is_not_tracked(self):
        sub = subject.track_subject([[], [], [self.box(0.5)]])
        self.assertEqual(sub["tracked"], [False, False, True])

    def test_car_scene_follows_the_car(self):
        dets = [[self.box(0.5, w=0.25, h=0.2, s=0.7, label="car"), self.box(0.3, s=0.9)] for _ in range(8)]
        sub = subject.track_subject(dets)
        self.assertEqual(set(sub["label"]), {"car"})

    def test_a_lost_track_comes_back_as_the_same_track(self):
        dets = [[self.box(0.5)], [self.box(0.52)], [], [self.box(0.55)], [self.box(0.56)]]
        sub = subject.track_subject(dets)
        self.assertEqual(len(set(sub["track"])), 1)

    def test_no_detection_never_becomes_a_centre_guess(self):
        sub = subject.track_subject([[] for _ in range(5)])
        self.assertFalse(any(sub["tracked"]))
        self.assertTrue(all(b is None for b in sub["box"]))
