import hashlib
import tempfile
import unittest

import numpy as np

from clip_engine import detector


def raw_with(cells, n_cls=80):
    """Fake YOLOX output (1, 8400, 85): cells = [(index, cx_px, cy_px, w_px, h_px, class_id, score)] in letterbox pixels."""
    g, s = detector.grids()
    out = np.zeros((1, len(g), 5 + n_cls), np.float32)
    out[:, :, 2:4] = np.log(1.0)          # tiny boxes, no objectness: never above the threshold
    for idx, cx, cy, w, h, cid, sc in cells:
        out[0, idx, 0] = cx / s[idx, 0] - g[idx, 0]
        out[0, idx, 1] = cy / s[idx, 0] - g[idx, 1]
        out[0, idx, 2] = np.log(w / s[idx, 0])
        out[0, idx, 3] = np.log(h / s[idx, 0])
        out[0, idx, 4] = 1.0
        out[0, idx, 5 + cid] = sc
    return out


class Decode(unittest.TestCase):
    def test_grid_size(self):
        g, s = detector.grids()
        self.assertEqual(len(g), 8400)
        self.assertEqual(s.shape, (8400, 1))

    def test_person_and_car_decoded_normalised(self):
        # frame 960x540 letterboxed into 640: ratio 2/3
        r = 640 / 960
        i1, i2 = 100, 6400 + 300
        raw = raw_with([(i1, 320, 200, 60, 120, 0, 0.9), (i2, 100, 300, 80, 40, 2, 0.8)])
        res = detector.decode(raw, r, 960, 540)
        labels = sorted(b[5] for b in res)
        self.assertEqual(labels, ["car", "person"])
        for b in res:
            self.assertTrue(0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1)

    def test_unwanted_class_is_ignored(self):
        raw = raw_with([(100, 320, 200, 60, 120, 16, 0.95)])       # 16 = dog
        self.assertEqual(detector.decode(raw, 1.0, 640, 640), [])

    def test_low_score_is_ignored(self):
        raw = raw_with([(100, 320, 200, 60, 120, 0, 0.1)])
        self.assertEqual(detector.decode(raw, 1.0, 640, 640), [])

    def test_nms_keeps_one_of_two_overlapping(self):
        b = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], float)
        self.assertEqual(sorted(detector.nms(b, np.array([0.9, 0.8, 0.7]))), [0, 2])

    def test_wrong_output_size_is_an_error(self):
        with self.assertRaises(ValueError):
            detector.decode(np.zeros((1, 10, 85), np.float32), 1.0, 640, 640)

    def test_letterbox_shape_and_padding(self):
        x, r = detector.letterbox(np.zeros((540, 960, 3), np.uint8))
        self.assertEqual(x.shape, (1, 3, 640, 640))
        self.assertAlmostEqual(r, 640 / 960)
        self.assertEqual(float(x[0, 0, 639, 639]), 114.0)


class Model(unittest.TestCase):
    def test_pinned_hash_is_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(detector.ModelError):
                detector.ensure_model(d, opener=lambda u: b"not the model")

    def test_good_file_is_cached(self):
        data = b"model"
        old = detector.MODEL["sha256"]
        detector.MODEL["sha256"] = hashlib.sha256(data).hexdigest()
        try:
            with tempfile.TemporaryDirectory() as d:
                p = detector.ensure_model(d, opener=lambda u: data)
                p2 = detector.ensure_model(d, opener=lambda u: (_ for _ in ()).throw(AssertionError("downloaded twice")))
                self.assertEqual(p, p2)
        finally:
            detector.MODEL["sha256"] = old

    def test_pin_is_a_sha256_and_url_is_https(self):
        self.assertEqual(len(detector.MODEL["sha256"]), 64)
        self.assertTrue(detector.MODEL["url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
