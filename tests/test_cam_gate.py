import unittest

from clip_engine import cam_sr, grade_checks

def cq(**kw):
    base = {"enhanced": {"fidelity": 0.97, "blockiness": 0.98, "edge": 30.0},
            "lanczos": {"fidelity": 0.99, "blockiness": 1.0, "edge": 22.0},
            "method": "realesrgan", "blend": 0.7, "cam_fps": 15, "interp": "flow", "sr_seconds": 400.0}
    base.update(kw)
    return base


def meas(c, layout="cam_top"):
    return {"render": {"layout": layout, "cam_quality": c}}


class GraderGate(unittest.TestCase):
    def test_pass_when_all_three_hold(self):
        r = grade_checks.check_cam_quality(meas(cq()))
        self.assertEqual(r["status"], grade_checks.PASS, r["detail"])

    def test_each_metric_can_fail_it_with_a_reason(self):
        cases = (({"enhanced": {"fidelity": 0.95, "blockiness": 0.98, "edge": 30.0}}, "fidelity"),
                 ({"enhanced": {"fidelity": 0.97, "blockiness": 1.0, "edge": 30.0}}, "blockiness"),
                 ({"enhanced": {"fidelity": 0.97, "blockiness": 0.98, "edge": 22.0}}, "edge"))
        for kw, word in cases:
            r = grade_checks.check_cam_quality(meas(cq(**kw)))
            self.assertEqual(r["status"], grade_checks.FAIL)
            self.assertIn(word, r["detail"].split("|")[-1])

    def test_missing_measurement_fails_and_no_cam_is_na(self):
        self.assertEqual(grade_checks.check_cam_quality(meas(None))["status"], grade_checks.FAIL)
        self.assertEqual(grade_checks.check_cam_quality(meas(None, layout="full"))["status"], grade_checks.NA)

    def test_it_is_part_of_the_grade(self):
        self.assertIn(grade_checks.check_cam_quality, grade_checks.ALL)

    def test_ghosting_fails_the_check_even_when_the_numbers_pass(self):
        r = grade_checks.check_cam_quality(meas(cq(ghost={"ok": False, "detail": "in-between mean 0.80"})))
        self.assertEqual(r["status"], grade_checks.FAIL)
        self.assertIn("double edges", r["detail"].split("|")[-1])

    def test_ghost_ok_and_old_chain_blurred_below_one_passes(self):
        c = cq(enhanced={"fidelity": 0.981, "blockiness": 1.003, "edge": 14.03},
               lanczos={"fidelity": 0.993, "blockiness": 0.911, "edge": 11.97}, ghost={"ok": True, "detail": "fine"})
        r = grade_checks.check_cam_quality(meas(c))
        self.assertEqual(r["status"], grade_checks.PASS, r["detail"])

    def test_a_real_block_grid_fails_even_against_a_blurred_old_chain(self):
        c = cq(enhanced={"fidelity": 0.981, "blockiness": 1.08, "edge": 14.03},
               lanczos={"fidelity": 0.993, "blockiness": 0.911, "edge": 11.97})
        self.assertEqual(grade_checks.check_cam_quality(meas(c))["status"], grade_checks.FAIL)


class Verdict(unittest.TestCase):
    def test_verdict_lists_every_reason(self):
        ok, reasons = cam_sr.quality_verdict(cq(enhanced={"fidelity": 0.9, "blockiness": 1.5, "edge": 1.0}, ghost={"ok": False}))
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 4)

    def test_verdict_ok(self):
        self.assertEqual(cam_sr.quality_verdict(cq())[0], True)


if __name__ == "__main__":
    unittest.main()
