"""cam_enhance on a REAL video: ffmpeg makes a small 1080p h264 mpegts file (the cached source's format) and the
real prepare()/CamStream decode it. Fake arrays never exercised ffmpeg's own crop (it rounds odd sizes down on
4:2:0), which is how run 45026217442 died on a 393 px wide ROI. Needs ffmpeg + OpenCV; skipped without them."""
import os
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

from clip_engine import cam_enhance, cam_sr

try:
    import cv2
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
REGION = (0, 432, 393, 653)          # the real cam region of the verification source (393 px wide: odd)
SRC_WH = (1920, 1080)


class FakeUp:
    name = "fake"

    def run4(self, img):
        out = cv2.resize(img, None, fx=4, fy=4, interpolation=cv2.INTER_LANCZOS4)
        return cam_sr.sharpen(out, 0.5, 1.0)


class Geometry(unittest.TestCase):
    def test_roi_is_even_everywhere_for_the_real_region_and_odd_inputs(self):
        for boxes in ([(0.0, 438.0, 360.0, 180.0)], [(0.3, 441.7, 360.0, 180.0), (27.9, 470.2, 360.0, 180.0)],
                      [(33.0, 473.0, 360.0, 180.0)]):
            for region in (REGION, (1, 431, 394, 653), (0, 432, 393, 652)):
                x, y, w, h = cam_enhance.roi_union(boxes, region, src_wh=SRC_WH)
                self.assertEqual((x % 2, y % 2, w % 2, h % 2), (0, 0, 0, 0), (boxes, region, (x, y, w, h)))
                self.assertGreaterEqual(x, region[0])
                self.assertLessEqual(x + w, region[2])
                self.assertLessEqual(y + h, region[3])
                if region == REGION:            # the odd-origin regions cannot hold boxes that start left of them
                    self.assertLessEqual(x, min(b[0] for b in boxes) + 1)
                    self.assertGreaterEqual(x + w, max(b[0] + b[2] for b in boxes) - 1)


@unittest.skipUnless(HAVE_FFMPEG and HAVE_CV2, "ffmpeg and OpenCV are needed for the real-video test")
class RealVideo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.media = os.path.join(cls.tmp, "source.mp4")        # like out/source.mp4: .mp4 name, mpegts container
        p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=60",
                            "-t", "5", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-f", "mpegts",
                            cls.media], capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(f"could not make the test video: {p.stderr[-300:]!r}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def boxes(self, n):
        # the track drifts to the region's right/bottom edge (393 / 653): that union is ODD without the fix
        return [(0.27 * i, 438.0 + 0.25 * i, 360.0, 180.0) for i in range(n)]

    def test_grab_roi_cuts_the_exact_even_roi_out_of_the_real_video(self):
        roi = cam_enhance.roi_union(self.boxes(30), REGION, src_wh=SRC_WH)
        img = cam_enhance.grab_roi(self.media, 2.0, roi, "", cam_sr.DENOISE_VF)
        self.assertIsNotNone(img)
        self.assertEqual(img.shape, (roi[3], roi[2], 3))

    def test_an_odd_roi_is_what_broke_the_render(self):
        """ffmpeg really returns a smaller frame for an odd crop: that is why the ROI must be even."""
        self.assertIsNone(cam_enhance.grab_roi(self.media, 2.0, (0, 432, 393, 221)))
        msg = cam_enhance.grab_failure(self.media, 2.0, (0, 432, 393, 221), src_wh=SRC_WH, region=REGION, start=1.0, dur=2.0)
        self.assertIn("ODD ROI", msg)
        self.assertIn("393x221", msg)
        self.assertIn("expected 260559", msg)

    def test_prepare_then_stream_on_the_real_video(self):
        fps, dur, start = 60.0, 2.0, 1.0
        n = int(dur * fps)
        stream, info = cam_enhance.prepare(self.media, start, dur, fps, "60", self.boxes(n), REGION, SRC_WH,
                                           {"blend": 0.7, "deblock_vf": ""}, FakeUp())
        try:
            self.assertEqual(info["roi"][2] % 2 + info["roi"][3] % 2, 0)
            for i in (0, 1, 7, n // 2, n - 1):
                blk = stream.block(i)
                self.assertEqual(blk.shape, (540, 1080, 3))
                self.assertGreater(float(blk.std()), 5.0)       # a real picture, not a flat fill
        finally:
            stream.close()

    def test_a_missing_source_gives_an_error_that_explains_itself(self):
        with self.assertRaises(ValueError) as cm:
            cam_enhance.prepare(os.path.join(self.tmp, "nope.mp4"), 1.0, 2.0, 60.0, "60", self.boxes(120), REGION, SRC_WH,
                                {"blend": 0.7, "deblock_vf": ""}, FakeUp())
        msg = str(cm.exception)
        for part in ("could not cut the cam ROI", "crop ", "start=1.0", "dur=2.0", "ffmpeg exit", "expected", "stderr tail"):
            self.assertIn(part, msg)

    def test_prepass_with_no_frames_reports_roi_window_and_stderr(self):
        roi = cam_enhance.roi_union(self.boxes(4), REGION, src_wh=SRC_WH)
        stream = cam_enhance.CamStream(os.path.join(self.tmp, "nope.mp4"), 0.0, 1.0, 60.0, self.boxes(60), roi,
                                       {"cam_fps": 20, "blend": 0.7, "deblock_vf": "", "interp": "nearest"}, FakeUp(), "60")
        try:
            with self.assertRaises(RuntimeError) as cm:
                stream.block(0)
            msg = str(cm.exception)
            for part in ("no frames", f"ROI {roi[2]}x{roi[3]}", "window start=", "frames read 0", "stderr tail"):
                self.assertIn(part, msg)
        finally:
            stream.close()


if __name__ == "__main__":
    unittest.main()
