"""panel_check: the pre-render measurement must agree with the grade's rule (subject.classify_output, 95%)."""
import unittest

from clip_engine import panel_check as pc, subject

W = pc.GRADE_W


def det_ok():            # a person well inside the 540 px panel
    return [(120.0, 80.0, 400.0, 600.0, 0.9)]


def det_cut():           # a person touching the right edge: the grade calls the frame cut
    return [(150.0, 60.0, 539.0, 640.0, 0.9)]


def run(plan):
    """plan: list of 'ok' | 'cut' | 'unseen' per sample, through fake frames and a fake detector."""
    seq = {"ok": det_ok, "cut": det_cut, "unseen": lambda: []}
    frames = list(range(len(plan)))
    boxes = [(0, 0, 845, 1080)] * len(plan)
    times = [i / 2 for i in range(len(plan))]
    return pc.measure(frames, boxes, times, detect=lambda im, w: seq[plan[im]](), prep=lambda fr, bx: fr)


class Measure(unittest.TestCase):
    def test_all_inside_passes(self):
        m = run(["ok"] * 10)
        self.assertEqual(m["inside_pct"], 100.0)
        self.assertEqual(pc.verdict(m)[0], True)

    def test_golden_numbers_are_refused(self):
        # run 45518671433: 57 samples, 41 ok, 12 cut, 4 unseen -> 77.4% inside on 93% detected
        plan = ["ok"] * 41 + ["cut"] * 12 + ["unseen"] * 4
        m = run(plan)
        self.assertEqual(m["inside_pct"], 77.4)
        self.assertEqual(m["detected_pct"], 93.0)
        ok, text = pc.verdict(m)
        self.assertFalse(ok)
        self.assertIn("77.4%", text)
        self.assertIn("needs >= 95%", text)

    def test_same_rule_as_the_grade_check(self):
        for plan in (["ok"] * 19 + ["cut"], ["ok"] * 18 + ["cut"] * 2, ["ok"] * 5 + ["unseen"] * 15):
            m = run(plan)
            from clip_engine import grade_checks as gc
            res = gc.check_subject({"subject_out": {k: m[k] for k in ("inside_pct", "detected_pct", "cut_times", "unseen_times", "samples")}})
            self.assertEqual(pc.verdict(m)[0], res["status"] != gc.FAIL, plan)

    def test_unseen_frames_do_not_count_against_inside(self):
        m = run(["ok"] * 10 + ["unseen"] * 2)
        self.assertEqual(m["inside_pct"], 100.0)

    def test_too_few_detections_cannot_verify_and_is_not_a_refusal(self):
        m = run(["ok"] + ["unseen"] * 9)
        ok, text = pc.verdict(m)
        self.assertTrue(ok)
        self.assertIn("cannot verify", text)

    def test_one_edge_frame_in_twenty_is_exactly_at_the_bar(self):
        m = run(["ok"] * 19 + ["cut"])
        self.assertEqual(m["inside_pct"], 95.0)
        self.assertTrue(pc.verdict(m)[0])

    def test_cut_times_are_reported(self):
        m = run(["ok", "cut", "ok", "cut"])
        self.assertEqual(m["cut_times"], [0.5, 1.5])


class Rule(unittest.TestCase):
    def test_classify_matches_subject_module(self):
        self.assertEqual(subject.classify_output([det_ok(), det_cut(), []], W), ["ok", "cut", "unseen"])


if __name__ == "__main__":
    unittest.main()
