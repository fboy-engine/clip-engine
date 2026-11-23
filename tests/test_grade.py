import copy
import unittest

from clip_engine import grade_checks as gc, grade_clip as g

GOOD = {
    "video": {"width": 1080, "height": 1920, "codec": "h264", "pix_fmt": "yuv420p", "fps": 60.0,
              "frames": 2280, "duration": 38.0, "bit_rate_kbps": 14000, "color_range": "tv",
              "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709"},
    "audio": {"codec": "aac", "sample_rate": 48000, "duration": 38.0},
    "expected": {"frames": 2280, "duration": 38.0, "source_fps": 60.0},
    "crf": 17.0, "short_frames": 0,
    "loud": {"i": -14.2, "lra": 6.0, "tp": -1.6},
    "render": {"captions_check": {"coverage": 0.96, "speech_sec": 30.0, "covered_sec": 28.8, "second_source": True,
                                  "counts": {"corrected": 1, "inserted": 1, "unresolved": 0}, "unresolved": [],
                                  "gemini": {"available": True}, "ocr": {"available": False}},
               "audio_inputs": 1, "audio_filters": ["afade", "acompressor", "loudnorm", "alimiter"], "zoom_max": 0.05,
               "layout": "cam_top", "subject": {"inside_pct": 99.0, "tracked_pct": 80.0},
               "cam_quality": {"enhanced": {"fidelity": 0.97, "blockiness": 0.98, "edge": 30.0},
                               "lanczos": {"fidelity": 0.99, "blockiness": 1.0, "edge": 22.0},
                               "method": "realesrgan", "blend": 0.7, "cam_fps": 15, "interp": "flow", "sr_seconds": 400.0,
                               "ghost": {"ok": True, "detail": "fine"}},
               "cam_block": {"found": True, "corner": "bottom-left", "face_hit_pct": 88.0, "box_px": [504, 252], "upscale": 2.14},
               "crop_in_frame": True, "pan_max_px_s": 200.0, "pan_total_px": 300.0,
               "cam": {"rect": [0, .6, .25, 1], "excluded": True, "how": "assumed"},
               "hook": {"text": "Everyone dies today!", "source": "given", "match": True},
               "trim": {"end_reason": "payoff phrase matched"}},
    "ass": {"hook_start": 0.0, "hook_text": "Everyone dies today!", "caption_styles": ["Main"],
            "caption_lines": 12, "max_highlights": 1, "last_end": 37.5,
            "first_caption": "fine then watch this", "fonts": ["Anton"], "unmasked_profanity": [],
            "bands": [["Main", 0.2, 1.0, 487.5, 592.5], ["Hook", 0.0, 2.6, 700.0, 790.0]]},
    "visual": {"ok": True, "detail": "ok"}, "motion": {"ok": True, "detail": "ok"},
    "subject_out": {"inside_pct": 98.0, "detected_pct": 80.0, "samples": 76, "cut_times": [], "unseen_times": []},
    "balance": {"voice_db": -22.0, "loud_db": -12.0, "gap_db": 10.0},
    "opening": {"ok": True, "detail": "first 3.0 s: longest frozen 0.0 s, 100% of steps moving, first motion at 0.1 s"},
    "cold_open": {"onset_s": 0.0, "first_db": -20.0, "median_db": -22.0},
    "word_spans": [[0.4, 1.0], [36.8, 37.5]],
    "cut": {"start": 42.0, "end": 80.0},
}


def status(m, name):
    return next(c["status"] for c in gc.run_all(m) if c["name"] == name)


def mod(path, value):
    m = copy.deepcopy(GOOD)
    d = m
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] = value
    return m


class Checks(unittest.TestCase):
    def test_good_clip_passes_everything(self):
        cs = gc.run_all(GOOD)
        self.assertEqual([c for c in cs if c["status"] != gc.PASS], [])
        self.assertIn("GRADE: PASS", gc.format_report(cs))

    def test_each_rule_can_fail(self):
        self.assertEqual(status(mod(["video", "width"], 720), "video format"), gc.FAIL)
        self.assertEqual(status(mod(["video", "duration"], 50.0), "length 25-45 s"), gc.FAIL)
        self.assertEqual(status(mod(["video", "frames"], 2279), "frame-accurate trim"), gc.FAIL)
        self.assertEqual(status(mod(["audio", "duration"], 38.2), "frame-accurate trim"), gc.FAIL)
        self.assertEqual(status(mod(["crf"], 23.0), "x264 CRF 17"), gc.FAIL)
        self.assertEqual(status(mod(["crf"], None), "x264 CRF 17"), gc.WARN)
        self.assertEqual(status(mod(["render", "audio_inputs"], 2), "no added music/SFX"), gc.FAIL)
        self.assertEqual(status(mod(["render", "audio_filters"], ["loudnorm", "amix"]), "no added music/SFX"), gc.FAIL)
        self.assertEqual(status(mod(["ass", "hook_start"], 1.4), "hook within 1 s"), gc.FAIL)
        self.assertEqual(status(mod(["ass", "hook_text"], ""), "hook within 1 s"), gc.FAIL)
        self.assertEqual(status(mod(["ass", "max_highlights"], 2), "one caption style, max 1 highlight"), gc.FAIL)
        self.assertEqual(status(mod(["ass", "caption_styles"], ["Main", "Pop"]), "one caption style, max 1 highlight"), gc.FAIL)
        self.assertEqual(status(mod(["render", "zoom_max"], 0.13), "tracked crop + punch-in"), gc.FAIL)
        self.assertEqual(status(mod(["render", "zoom_max"], 0.10), "tracked crop + punch-in"), gc.WARN)
        self.assertEqual(status(mod(["render", "crop_in_frame"], False), "tracked crop + punch-in"), gc.FAIL)
        self.assertEqual(status(mod(["visual", "ok"], False), "visual gate"), gc.FAIL)
        self.assertEqual(status(mod(["motion", "ok"], False), "motion gate"), gc.FAIL)

    def test_new_rules(self):
        self.assertEqual(status(mod(["video", "color_range"], "pc"), "video format"), gc.FAIL)
        self.assertEqual(status(mod(["video", "color_space"], None), "video format"), gc.FAIL)
        bad = {"inside_pct": 90.0, "detected_pct": 80.0, "samples": 76, "cut_times": [3.5, 4.0], "unseen_times": []}
        self.assertEqual(status(mod(["subject_out"], bad), "subject inside panel >= 95%"), gc.FAIL)
        c = next(c for c in gc.run_all(mod(["subject_out"], bad)) if c["name"].startswith("subject"))
        self.assertIn("3.5s", c["detail"])
        low = {"inside_pct": 100.0, "detected_pct": 10.0, "samples": 76, "cut_times": [], "unseen_times": []}
        self.assertEqual(status(mod(["subject_out"], low), "subject inside panel >= 95%"), gc.WARN)
        self.assertEqual(status(mod(["subject_out"], {"error": "x"}), "subject inside panel >= 95%"), gc.WARN)
        self.assertEqual(status(mod(["balance", "gap_db"], 18.0), "voice vs game audio"), gc.WARN)
        self.assertEqual(status(mod(["render", "layout"], "full"), "layout: cam on top, bigger gameplay below"), gc.WARN)

    def test_key_numbers_has_the_run_summary_fields(self):
        k = gc.key_numbers(GOOD)
        for s in ("1080x1920", "60.0 fps", "14000 kbps", "-14.2 LUFS", "cam found: yes", "subject in frame: 98.0%"):
            self.assertIn(s, k)

    def test_loudness_bands(self):
        self.assertEqual(status(mod(["loud", "i"], -15.5), "loudness -14 LUFS"), gc.WARN)
        self.assertEqual(status(mod(["loud", "i"], -18.0), "loudness -14 LUFS"), gc.FAIL)
        self.assertEqual(status(mod(["loud", "tp"], -0.2), "loudness -14 LUFS"), gc.FAIL)
        self.assertEqual(status(mod(["loud", "i"], None), "loudness -14 LUFS"), gc.FAIL)

    def test_unmeasured_is_not_a_pass(self):
        m = copy.deepcopy(GOOD)
        del m["visual"], m["motion"]
        self.assertEqual(status(m, "visual gate"), gc.NA)
        self.assertEqual(status({"expected": {}}, "tracked crop + punch-in"), gc.WARN)

    def test_cam_inside_crop_warns(self):
        m = mod(["render", "cam"], {"rect": [0, .6, .25, 1], "excluded": False, "how": "assumed"})
        self.assertEqual(status(m, "tracked crop + punch-in"), gc.WARN)

    def test_dead_tracking_warns(self):
        self.assertEqual(status(mod(["render", "pan_total_px"], 0.0), "tracked crop + punch-in"), gc.WARN)


EBUR = """
[Parsed_ebur128_0 @ 0x1] t: 5  TARGET:-23 LUFS    M: -14.0 S: -14.1     I: -14.3 LUFS       LRA:   2.0 LU  FTPK: -3.0 -3.1 dBFS  TPK: -2.0 -2.0 dBFS
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -14.1 LUFS
    Threshold: -24.3 LUFS

  Loudness range:
    LRA:         5.2 LU
    Threshold: -34.3 LUFS

  True peak:
    Peak:       -1.6 dBFS
"""

ASS = """[Script Info]

[Events]
Format: Layer,Start,End,Style,Text
Dialogue: 0,0:00:00.50,0:00:02.00,Main,hello {\\c&H0000D7FF&}world{\\c&HFFFFFF&} now
Dialogue: 0,0:00:02.10,0:00:03.50,Main,plain words here
Dialogue: 1,0:00:00.00,0:00:02.60,Hook,Everyone dies today!
"""


class Parsers(unittest.TestCase):
    def test_ebur128_summary_not_frame_log(self):
        self.assertEqual(g.parse_ebur128(EBUR), {"i": -14.1, "lra": 5.2, "tp": -1.6})

    def test_ass(self):
        a = g.parse_ass(ASS)
        self.assertEqual(a["hook_start"], 0.0)
        self.assertEqual(a["hook_text"], "Everyone dies today!")
        self.assertEqual(a["caption_styles"], ["Main"])
        self.assertEqual(a["caption_lines"], 2)
        self.assertEqual(a["max_highlights"], 1)
        self.assertAlmostEqual(a["last_end"], 3.5)


if __name__ == "__main__":
    unittest.main()
