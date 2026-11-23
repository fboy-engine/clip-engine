"""cam-bench must honour the cam_rect hint exactly like render (run 45172106333: the hint never reached the bench,
which ran the generic finder on a whole-frame cam and failed)."""
import os
import tempfile
import unittest
from unittest import mock

from clip_engine import cam_bench, cli, camresolve, download, exclusion, srcinfo

HINT = [0.679, 0.0, 1.0, 0.393]


class _Stop(Exception):
    pass


class CamBenchRectTests(unittest.TestCase):
    def test_run_passes_cam_rect_to_resolve(self):
        seen = {}

        def fake_resolve(media, start, dur, w, h, out, zones, cam_rect, cam_corner, log):
            seen["cam_rect"], seen["cam_corner"] = cam_rect, cam_corner
            raise _Stop()

        pr = {"width": 1920, "height": 1080, "fps": 60.0, "duration": 19.0}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(download, "probe_media", return_value=pr), \
                mock.patch.object(srcinfo, "media_info", return_value={}), \
                mock.patch.object(srcinfo, "info_line", return_value=""), \
                mock.patch.object(exclusion, "zones_for", return_value={}), \
                mock.patch.object(camresolve, "resolve", side_effect=fake_resolve):
            with self.assertRaises(_Stop):
                cam_bench.run("x.mp4", 2.0, 19.0, d, log=lambda *_: None, cam_rect=HINT)
        self.assertEqual(seen["cam_rect"], HINT)
        self.assertIsNone(seen["cam_corner"])

    def test_run_without_hint_stays_auto(self):
        seen = {}

        def fake_resolve(media, start, dur, w, h, out, zones, cam_rect, cam_corner, log):
            seen["cam_rect"] = cam_rect
            raise _Stop()

        pr = {"width": 1920, "height": 1080, "fps": 60.0, "duration": 19.0}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(download, "probe_media", return_value=pr), \
                mock.patch.object(srcinfo, "media_info", return_value={}), \
                mock.patch.object(srcinfo, "info_line", return_value=""), \
                mock.patch.object(exclusion, "zones_for", return_value={}), \
                mock.patch.object(camresolve, "resolve", side_effect=fake_resolve):
            with self.assertRaises(_Stop):
                cam_bench.run("x.mp4", 2.0, 19.0, d, log=lambda *_: None)
        self.assertIsNone(seen["cam_rect"])

    def test_cli_parses_cam_rect(self):
        got = {}

        def fake_run(media, start, end, out, **kw):
            got.update(kw)
            return {}, "md"

        with tempfile.TemporaryDirectory() as d, mock.patch.object(cam_bench, "run", side_effect=fake_run):
            rc = cli.main(["cam-bench", "x.mp4", "--start", "2", "--end", "19",
                           "--cam-rect", "0.679,0,1,0.393", "--out", d])
        self.assertEqual(rc, 0)
        self.assertEqual(got["cam_rect"], HINT)

    def test_cli_without_cam_rect_is_none(self):
        got = {}

        def fake_run(media, start, end, out, **kw):
            got.update(kw)
            return {}, "md"

        with tempfile.TemporaryDirectory() as d, mock.patch.object(cam_bench, "run", side_effect=fake_run):
            cli.main(["cam-bench", "x.mp4", "--start", "2", "--end", "19", "--out", d])
        self.assertIsNone(got["cam_rect"])

    def test_workflow_passes_the_hint_to_cam_bench(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, ".github", "workflows", "clip.yml")) as f:
            text = f.read()
        i = text.index("cli cam-bench")
        step = text[text.rfind("- name:", 0, i):i + 200]
        self.assertIn("CAMRECT: ${{ inputs.cam_rect }}", step)
        self.assertIn('--cam-rect "$CAMRECT"', step)


if __name__ == "__main__":
    unittest.main()
