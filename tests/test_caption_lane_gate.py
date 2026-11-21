"""Slice C: profanity mask, subtitle reference, seam lane, text-over-text gate."""
import unittest

from clip_engine import asr_fix, caption_lane, captions, grade_checks, hooks


def W(w, s, e):
    return {"w": w, "s": s, "e": e, "p": 0.9}


class Profanity(unittest.TestCase):
    def test_masks_keep_first_letter_punct_and_times(self):
        out = asr_fix.mask_profanity([W("fuck", 0, .3), W("Shit!", .4, .7), W("fucking,", .8, 1.0), W("duck", 1.1, 1.2)])
        self.assertEqual([w["w"] for w in out], ["f***", "S***!", "f******,", "duck"])
        self.assertEqual(out[0]["s"], 0)

    def test_masked_hook_is_still_a_clean_hook(self):
        self.assertEqual(hooks.clean_hook("what f*** you man"), "what f*** you man")


class Reference(unittest.TestCase):
    def test_replaces_same_length_blocks_only(self):
        ws = [W("Female", 0, .4), W("muscular", .5, 1.0), W("in", 2, 2.1), W("her", 2.1, 2.3), W("20s", 2.3, 2.6), W("nah", 2.7, 3)]
        out, n = asr_fix.apply_reference(ws, "Famous muscular in her 20s nah")
        self.assertEqual(out[0]["w"], "Famous")
        self.assertEqual(n, 1)
        self.assertEqual([w["s"] for w in out], [w["s"] for w in ws])     # timing untouched

    def test_no_reference_changes_nothing(self):
        ws = [W("hello", 0, 1)]
        self.assertEqual(asr_fix.apply_reference(ws, ""), (ws, 0))


class Seam(unittest.TestCase):
    def ass(self, seam=None, hook=True):
        a = captions.to_ass([W("one", 0.2, 0.6), W("two", 0.6, 1.0)], 0.0, 1080, 1920, 420, seam)
        return hooks.add_hook(a, "hook text here", 300) if hook else a

    def test_seam_lane_is_centered_on_the_seam(self):
        lane = caption_lane.lanes(True, None, cam_top=True)
        self.assertEqual(lane["seam_y"], 540)
        bands = captions.ass_bands(self.ass(540))
        main = [b for b in bands if b[0] == "Main"]
        self.assertTrue(all(abs((b[3] + b[4]) / 2 - 540) < 1 for b in main))

    def test_hook_and_seam_captions_do_not_overlap(self):
        self.assertEqual(captions.band_overlaps(captions.ass_bands(self.ass(540))), [])

    def test_bottom_lane_without_cam(self):
        bands = captions.ass_bands(self.ass(None))
        main = [b for b in bands if b[0] == "Main"][0]
        self.assertAlmostEqual(main[4], 1920 - 420, delta=1)

    def test_overlap_gate_fails_text_over_text(self):
        a = captions.to_ass([W("one", 0.2, 0.6)], 0.0, 1080, 1920, 420, 300)      # caption on the hook's lane
        m = {"ass": {"bands": captions.ass_bands(hooks.add_hook(a, "hook text here", 300))}, "render": {"layout": "full"}}
        self.assertEqual(grade_checks.check_caption_lane(m)["status"], "fail")

    def test_gate_requires_the_seam_when_the_cam_is_on_top(self):
        m = {"ass": {"bands": captions.ass_bands(self.ass(None, hook=False))}, "render": {"layout": "cam_top"}}
        self.assertEqual(grade_checks.check_caption_lane(m)["status"], "fail")
        m = {"ass": {"bands": captions.ass_bands(self.ass(540))}, "render": {"layout": "cam_top"}}
        self.assertEqual(grade_checks.check_caption_lane(m)["status"], "pass")


if __name__ == "__main__":
    unittest.main()
