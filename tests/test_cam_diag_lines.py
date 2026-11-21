"""Cam diagnostics are reported per corner, with the seed rect when a person gave one."""
import unittest

from clip_engine import render_clip


def diag(seed=False):
    c = {"strict": 1, "relaxed": 2, "max_face_px": 40, "strict_pct": 9.1}
    d = {"frames": 11, "corners": {k: dict(c) for k in ("top-left", "top-right", "bottom-left", "bottom-right")}}
    if seed:
        d["seed"] = {"rect": [0.7, 0.0, 1.0, 0.3], "strict": 4, "strict_pct": 36.4, "max_face_px": 55}
    return d


class DiagLines(unittest.TestCase):
    def test_one_line_per_corner(self):
        rows = render_clip.diag_lines(diag())
        self.assertEqual(len(rows), 4)
        self.assertIn("bottom-left: strict 1/11 (9.1%), relaxed 2, max face 40 px", rows)

    def test_seed_rect_is_reported(self):
        rows = render_clip.diag_lines(diag(seed=True))
        self.assertEqual(len(rows), 5)
        self.assertIn("seed rect [0.7, 0.0, 1.0, 0.3]", rows[-1])

    def test_failure_is_reported_not_hidden(self):
        self.assertIn("cam diagnostics failed: boom", render_clip.diag_lines({"error": "boom"})[0])


if __name__ == "__main__":
    unittest.main()
