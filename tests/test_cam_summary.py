import hashlib
import os
import tempfile
import unittest

import numpy as np

from clip_engine import cam_enhance, render_clip

try:
    import cv2
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False

def cq(**kw):
    base = {"enhanced": {"fidelity": 0.97, "blockiness": 0.98, "edge": 30.0},
            "lanczos": {"fidelity": 0.99, "blockiness": 1.0, "edge": 22.0},
            "method": "realesrgan", "blend": 0.7, "cam_fps": 15, "interp": "flow", "sr_seconds": 400.0}
    base.update(kw)
    return base


def meas(c, layout="cam_top"):
    return {"render": {"layout": layout, "cam_quality": c}}


class Summary(unittest.TestCase):
    def test_summary_has_source_clip_hash_and_cam_numbers(self):
        r = {"source_format": {"vcodec": "h264", "profile": "High", "pix_fmt": "yuv420p", "width": 1920, "height": 1080, "fps": 60.0,
                               "video_kbps": 5433, "container": "mpegts"},
             "clip_file": {"sha256": "ab" * 32, "width": 1080, "height": 1920, "fps": 60.0, "video_kbps": 9000, "size_mb": 40.1},
             "cam_quality": dict(cq(), gate={"ok": True, "reasons": []}, deblock="none")}
        md = render_clip.summary_md(r)
        for want in ("5433 kbps", "sha256", "1080x1920", "realesrgan", "PASS"):
            self.assertIn(want, md)
        self.assertEqual(render_clip.summary_md({}), "")

    def test_summary_shows_a_failed_gate_with_the_reason(self):
        r = {"cam_quality": cq(enhanced={"fidelity": 0.9, "blockiness": 0.98, "edge": 30.0}, deblock="none")}
        md = render_clip.summary_md(r)
        self.assertIn("FAIL", md)
        self.assertIn("fidelity", md)


class ClipFile(unittest.TestCase):
    def test_sha256_resolution_and_bitrate_of_the_clip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "clip.mp4")
            data = os.urandom(300000)
            with open(p, "wb") as f:
                f.write(data)
            probe = lambda path: {"width": 1080, "height": 1920, "fps": 60.0, "video_kbps": 9123, "size_mb": 0.3, "duration": 40.0, "vcodec": "h264"}
            info = render_clip.clip_file_info(p, probe)
        self.assertEqual(info["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual((info["width"], info["height"], info["video_kbps"]), (1080, 1920, 9123))


@unittest.skipUnless(HAVE_CV2, "OpenCV not installed here")
class ComparePng(unittest.TestCase):
    def test_old_over_new_at_the_real_block_size(self):
        a, b = np.zeros((540, 1080, 3), np.uint8), np.full((540, 1080, 3), 200, np.uint8)
        probe = {"pairs": [(10, a, b), (900, b, a)]}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "cam_compare.png")
            self.assertEqual(cam_enhance.write_compare(probe, p), 2)
            img = cv2.imread(p)
        self.assertEqual(img.shape, (4 * 540, 1080, 3))

    def test_nothing_to_show_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.png")
            self.assertEqual(cam_enhance.write_compare({"pairs": []}, p), 0)
            self.assertFalse(os.path.exists(p))


if __name__ == "__main__":
    unittest.main()
