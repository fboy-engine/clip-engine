import json
import os
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

from clip_engine import grade_checks, grade_clip, render_clip as rc


class Geometry(unittest.TestCase):
    def test_frame_window_snaps_to_the_grid(self):
        s, n, d = rc.frame_window(42.017, 80.0, 60)
        self.assertAlmostEqual(s * 60, round(s * 60), places=6)
        self.assertEqual(n, round((80.0 - 42.017) * 60))
        self.assertAlmostEqual(d, n / 60)

    def test_fps_arg(self):
        self.assertEqual(rc.fps_arg(60), "60")
        self.assertEqual(rc.fps_arg(59.94), "60000/1001")

    def test_path_is_clamped_and_smooth(self):
        pts = [(0, 100.0), (0.5, 900.0), (1.0, 100.0), (1.5, 900.0), (2.0, 100.0)]
        x = rc.per_frame_x(pts, 120, 60, (480.0, 1312.0))
        self.assertEqual(len(x), 120)
        self.assertTrue(((x >= 480.0) & (x <= 1312.0)).all())
        raw = np.interp(np.arange(120) / 60, [p[0] for p in pts], [p[1] for p in pts])
        self.assertLess(np.abs(np.diff(x)).max(), np.abs(np.diff(raw)).max())

    def test_boxes_ratio_zoom_and_frame(self):
        zs = [0.0, 0.04, 0.08]
        bx = rc.boxes([0, 656, 1312], zs, 1920, 1080)
        for (left, top, w, h), z in zip(bx, zs):
            self.assertAlmostEqual(w / h, 9 / 16, places=6)
            self.assertAlmostEqual(1080 / h - 1, z, places=6)
            self.assertGreaterEqual(left, 0)
            self.assertLessEqual(left + w, 1920 + 1e-6)
            self.assertGreaterEqual(top, 0)
        st = rc.path_stats(bx, 60, 1920, 1080, 1080)
        self.assertAlmostEqual(st["zoom_max"], 0.08, places=3)
        self.assertTrue(st["crop_in_frame"])

    def test_cam_lower_bound_is_respected_with_zoom(self):
        bx = rc.boxes([540.0] * 5, [0, 0.02, 0.04, 0.06, 0.08], 1920, 1080, lo=540.0)
        self.assertTrue(all(b[0] >= 540.0 - 1e-6 for b in bx))

    def test_affine_maps_box_to_output(self):
        b = (500.0, 20.0, 600.0, 1066.67)
        M = rc.affine(b)
        tl = M @ np.array([b[0], b[1], 1.0])
        br = M @ np.array([b[0] + b[2], b[1] + b[3], 1.0])
        self.assertTrue(np.allclose(tl, [0, 0], atol=1e-3))
        self.assertTrue(np.allclose(br, [1080, 1920], atol=1.5))


class Commands(unittest.TestCase):
    LN = {"input_i": -20.1, "input_tp": -3.0, "input_lra": 5.0, "input_thresh": -30.0, "target_offset": 0.5}

    def test_parse_loudnorm(self):
        text = 'blah\n{\n\t"input_i" : "-20.10",\n\t"input_tp" : "-3.00",\n\t"input_lra" : "5.00",\n' \
               '\t"input_thresh" : "-30.00",\n\t"output_i" : "-14.0",\n\t"target_offset" : "0.50"\n}\n'
        self.assertEqual(rc.parse_loudnorm(text), self.LN)
        with self.assertRaises(ValueError):
            rc.parse_loudnorm("nothing")

    def test_loudnorm_second_pass_is_linear_with_measured_values(self):
        f = rc.loudnorm_filter(38.0, self.LN)
        for s in ("I=-14", "TP=-1.5", "measured_I=-20.1", "linear=true", "afade=t=in", "afade=t=out:st=37.988"):
            self.assertIn(s, f)

    def test_encode_command_follows_the_style_rules(self):
        c = rc.encode_cmd("src.mp4", 42.0, 38.0, "out/clip.ass", "out/clip.mp4", 60, "afade=x,loudnorm=I=-14")
        j = " ".join(c)
        self.assertIn("-crf 17", j)
        self.assertIn("libx264", j)
        self.assertEqual(c.count("-i"), 2)                    # raw frames + the stream: no third audio source
        self.assertEqual(c.count("-map"), 2)
        self.assertIn("-map 1:a:0", j)
        for bad in ("amix", "amovie", "anoisesrc", "sine", "music"):
            self.assertNotIn(bad, j)
        self.assertIn("ass=out/clip.ass", j)
        self.assertIn("-ar 48000", j)

    def test_decode_limits_frames(self):
        c = rc.decode_cmd("src.mp4", 42.0, 2280, 60)
        self.assertEqual(c[c.index("-frames:v") + 1], "2280")
        self.assertLess(c.index("-ss"), c.index("-i"))


E2E = os.environ.get("CLIP_E2E") == "1" and shutil.which("ffmpeg") and shutil.which("ffprobe")


@unittest.skipUnless(E2E, "set CLIP_E2E=1 with ffmpeg + opencv installed")
class EndToEnd(unittest.TestCase):
    def test_synthetic_render_and_grade(self):
        d = tempfile.mkdtemp()
        src = os.path.join(d, "source.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=1920x1080:r=30:d=30", "-f", "lavfi", "-i",
                        "sine=f=300:r=48000:d=30", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", src], check=True)
        words = [{"w": w, "s": 1.0 + i * 0.4, "e": 1.35 + i * 0.4, "p": 0.9}
                 for i, w in enumerate("Everyone dies today because we are very lucky tonight".split())]
        words[-1]["w"] = "tonight."
        out = os.path.join(d, "out")
        r = rc.render(src, 1.0, 28.0, out, hook_text="Everyone dies today!", cam_corner="bottom-left",
                      words=words, preset="ultrafast", log=lambda s: None)
        m, checks = grade_clip.grade(os.path.join(out, "clip.mp4"), os.path.join(out, "render.json"),
                                     os.path.join(out, "clip.ass"), out)
        by = {c["name"]: c for c in checks}
        for name in ("video format", "length 25-45 s", "frame-accurate trim", "x264 CRF 17", "hook within 1 s",
                     "one caption style, max 1 highlight", "no added music/SFX"):
            self.assertEqual(by[name]["status"], "pass", by[name])
        self.assertEqual(r["expected"]["frames"], m["video"]["frames"])
        self.assertLess(abs(m["loud"]["i"] - (-14.0)), 1.5)
        self.assertTrue(os.path.exists(os.path.join(out, "review")))


if __name__ == "__main__":
    unittest.main()


class ColorTags(unittest.TestCase):
    def test_vf_sets_all_bt709_tags_on_the_frames(self):
        cmd = rc.encode_cmd("m.mp4", 1.0, 30.0, "c.ass", "o.mp4", 60.0, "anull")
        vf = cmd[cmd.index("-vf") + 1]
        for tag in ("color_primaries=bt709", "color_trc=bt709", "colorspace=bt709", "range=tv"):
            self.assertIn(tag, vf)
