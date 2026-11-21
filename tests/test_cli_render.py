import unittest
from unittest import mock

from clip_engine import cli


class T(unittest.TestCase):
    def test_render_wires_args_and_grades(self):
        with mock.patch("clip_engine.render_clip.render", return_value={}) as r, \
                mock.patch.object(cli, "_grade_out", return_value=0) as g:
            rc = cli.main(["render", "out/source.mp4", "--start", "42", "--end", "80", "--hook", "Everyone dies today!",
                           "--cam-corner", "bottom-left", "--cam-rect", "0,0.6,0.25,1"])
        self.assertEqual(rc, 0)
        args = r.call_args[0]
        self.assertEqual(args[:6], ("out/source.mp4", 42.0, 80.0, "out", "Everyone dies today!", "bottom-left"))
        self.assertEqual(args[6], [0.0, 0.6, 0.25, 1.0])
        g.assert_called_once_with("out")

    def test_render_failure_is_reported_not_faked(self):
        with mock.patch("clip_engine.render_clip.render", side_effect=ValueError("clip would be 20 s")), \
                mock.patch.object(cli, "_grade_out") as g:
            self.assertEqual(cli.main(["render", "x.mp4", "--start", "1", "--end", "9"]), 1)
        g.assert_not_called()

    def test_failed_grade_fails_the_run(self):
        fake = [{"name": "x", "status": "fail", "detail": "d"}]
        with mock.patch("clip_engine.grade_clip.grade", return_value=({}, fake)):
            self.assertEqual(cli._grade_out("out"), 1)


if __name__ == "__main__":
    unittest.main()
