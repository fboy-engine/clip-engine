import unittest

import numpy as np

from clip_engine import (asr_fix, caption_lane, captions, cam_layout, gates, hooks, select_clean,
                         track)


def W(w, s, e, p=0.9):
    return {"w": w, "s": s, "e": e, "p": p}


class CamLayout(unittest.TestCase):
    def frames(self):
        rng = np.random.default_rng(0)
        f = rng.integers(0, 255, (30, 36, 64)).astype(np.uint8)
        f[:, 22:, :16] = 120            # static block, bottom-left (facecam)
        return f

    def test_static_rect_bottom_left(self):
        r = cam_layout.static_rect(self.frames(), "bottom-left")
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r[0], 0.0, delta=0.02)
        self.assertGreater(r[1], 0.55)
        self.assertAlmostEqual(r[2], 0.25, delta=0.03)
        self.assertIsNone(cam_layout.static_rect(self.frames(), "top-right"))

    def test_crop_bounds_avoid_left_cam(self):
        lo, hi = cam_layout.crop_bounds((0.0, 0.6, 0.25, 1.0), 1920, 608)
        self.assertGreaterEqual(lo, 0.25 * 1920)
        self.assertLessEqual(hi, 1920 - 608)
        self.assertEqual(cam_layout.crop_bounds(None, 1920, 608), (0.0, 1312.0))
        self.assertFalse(cam_layout.visible_in_crop((0, .6, .25, 1), 600, 608, 1920))
        self.assertTrue(cam_layout.visible_in_crop((0, .6, .25, 1), 100, 608, 1920))


class Track(unittest.TestCase):
    def test_centroid_follows_motion_and_ignores_masked_cam(self):
        f = np.zeros((20, 36, 64), dtype=np.uint8)
        for i in range(20):
            f[i, :, 48:56] = 255 * (i % 2)       # right side flickers
            f[i, 24:, :12] = 200 * (i % 2)       # facecam flickers bottom-left
        cx = track.centroid_x(f, mask_rect=(0, 0.6, 0.2, 1.0))
        self.assertGreater(np.mean(cx[1:]), 0.7)
        unmasked = track.centroid_x(f)
        self.assertLess(np.mean(unmasked[1:]), np.mean(cx[1:]))

    def test_plan_stays_inside_bounds_and_frame(self):
        f = np.zeros((40, 36, 64), dtype=np.uint8)
        for i in range(40):
            f[i, :, 2:6] = 255 * (i % 2)         # action at the far left
        p = track.plan(f, 4, 1920, 1080, bounds=(480.0, 1312.0))
        self.assertTrue(all(480.0 <= x <= 1312.0 for _, x in p["points"]))
        self.assertEqual(p["crop_h"], 1080)
        self.assertAlmostEqual(p["crop_w"] / p["crop_h"], 9 / 16, delta=0.003)
        self.assertEqual(p["points"][0][0], 0)

    def test_lerp_expr(self):
        e = track.lerp_expr([(0, 100), (1, 200), (2, 200)])
        self.assertTrue(e.startswith("if(lt(t,1)"))
        self.assertEqual(track.lerp_expr([(0, 5)]), "5")
        self.assertEqual(e.count("("), e.count(")"))


class SelectClean(unittest.TestCase):
    words = [W("I'm", 10.0, 10.3), W("gonna", 10.3, 10.7), W("be", 10.75, 10.9), W("careful.", 10.95, 11.5),
             W("Give", 12.0, 12.3), W("us", 12.3, 12.5)] + \
            [W("w%d" % i, 12.5 + i * 0.5, 12.9 + i * 0.5) for i in range(1, 66)] + \
            [W("today.", 46.0, 46.6), W("Okay", 47.8, 48.2)]

    def test_start_off_a_word_moves_into_the_gap(self):
        s, e, notes = select_clean.select_clean(self.words, 10.5, 46.5)
        self.assertNotEqual(s, 10.5)
        self.assertTrue(all(not (w["s"] < s < w["e"]) for w in self.words))
        self.assertTrue(any("start moved" in n for n in notes))

    def test_end_prefers_finished_sentence(self):
        s, e, notes = select_clean.select_clean(self.words, 12.0, 46.3)
        self.assertGreaterEqual(e, 46.6)
        self.assertLessEqual(e, 47.8)
        self.assertTrue(any("sentence" in n for n in notes))

    def test_never_breaks_length_rules(self):
        s, e, notes = select_clean.select_clean(self.words, 12.0, 37.2)
        self.assertTrue(25 <= e - s <= 45)


class Hooks(unittest.TestCase):
    def test_clean_hook(self):
        self.assertEqual(hooks.clean_hook("Everyone dies today!"), "Everyone dies today!")
        self.assertEqual(hooks.clean_hook("\U0001F525 NO WAY \U0001F525"), "NO WAY")
        self.assertEqual(hooks.clean_hook("one"), "")
        self.assertEqual(hooks.clean_hook("this is a way too long hook line"), "")

    def test_fallback_to_speech(self):
        words = [W("Oh", 1, 1.2), W("my", 1.2, 1.4), W("god.", 1.4, 1.8), W("Everyone", 2, 2.4),
                 W("dies", 2.4, 2.7), W("today.", 2.7, 3.2)]
        self.assertEqual(hooks.pick_hook("", words)[1], "speech")
        self.assertEqual(hooks.pick_hook("Everyone dies today!", words)[1], "gemini")

    def test_hook_in_first_second_and_same_font(self):
        ass = captions.to_ass([W("hello", 0.2, 0.6), W("world", 0.6, 1.0)])
        out = hooks.add_hook(ass, "Everyone dies today!")
        self.assertEqual(hooks.hook_start(out), "0:00:00.00")
        self.assertTrue(hooks.within_budget(hooks.hook_start(out)))
        self.assertEqual(out.count("DejaVu Sans"), 2)
        self.assertIn("Style: Hook", out)
        self.assertLess(out.index("Style: Hook"), out.index("[Events]"))
        self.assertEqual(hooks.add_hook(ass, ""), ass)


class AsrFix(unittest.TestCase):
    def test_fix_words(self):
        w = [W("lucia,", 1, 1.4), W("", 1.4, 1.5), W("...", 1.5, 1.6), W("lock", 2, 2.3),
             W("ghost", 3, 6.5, p=0.05)]
        out = asr_fix.fix_words(w, {"lucia": "Lucia"})
        self.assertEqual([x["w"] for x in out], ["Lucia,...", "lock"])   # stray punctuation glued on

    def test_to_clip_time(self):
        out = asr_fix.to_clip_time([W("a", 1, 2), W("b", 5, 6)], 4.5)
        self.assertEqual([x["w"] for x in out], ["b"])
        self.assertAlmostEqual(out[0]["s"], 0.5)


class Lanes(unittest.TestCase):
    def test_lane_defaults_and_cam(self):
        self.assertEqual(caption_lane.lanes()["caption_margin_v"], 420)
        self.assertGreater(caption_lane.lanes(True, (0, .6, .25, 1))["caption_margin_v"], 420)


class Gates(unittest.TestCase):
    def test_visual_gate(self):
        rng = np.random.default_rng(2)
        good = rng.integers(40, 200, (10, 36, 64)).astype(np.uint8)
        black = np.zeros((10, 36, 64), dtype=np.uint8)
        self.assertTrue(gates.visual_gate(good)[0])
        self.assertFalse(gates.visual_gate(black)[0])
        self.assertFalse(gates.visual_gate([])[0])

    def test_motion_gate(self):
        self.assertTrue(gates.motion_gate([0.02] * 30)[0])
        self.assertFalse(gates.motion_gate([0.02] * 5 + [0.0] * 10 + [0.02] * 5)[0])
        self.assertFalse(gates.motion_gate([])[0])


if __name__ == "__main__":
    unittest.main()
