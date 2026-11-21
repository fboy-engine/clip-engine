import types
import unittest

from clip_engine import vision


class FakeCascade:
    def __init__(self, path):
        self.path = path

    def empty(self):
        return False


def fake_cv2(version="4.10.0", top_level=True, nested=False, data=True, hog=True):
    m = types.SimpleNamespace(__version__=version)
    if top_level:
        m.CascadeClassifier = FakeCascade
    if nested:
        m.objdetect = types.SimpleNamespace(CascadeClassifier=FakeCascade)
    if data:
        m.data = types.SimpleNamespace(haarcascades="/x/")
    if hog:
        class H:
            def setSVMDetector(self, _d):
                pass
        m.HOGDescriptor = H
        m.HOGDescriptor_getDefaultPeopleDetector = lambda: [0.0]
    for n in ("GaussianBlur", "addWeighted", "warpAffine", "resize", "equalizeHist"):
        setattr(m, n, lambda *a, **k: None)
    return m


class Detectors(unittest.TestCase):
    def test_top_level_cascade(self):
        c = vision.load_face_cascade(fake_cv2())
        self.assertTrue(c.path.endswith("haarcascade_frontalface_default.xml"))

    def test_falls_back_to_objdetect_module(self):
        c = vision.load_face_cascade(fake_cv2(top_level=False, nested=True))
        self.assertIsInstance(c, FakeCascade)

    def test_missing_cascade_is_a_clear_error_not_attributeerror(self):
        with self.assertRaises(vision.DetectorUnavailable) as cm:
            vision.load_face_cascade(fake_cv2("5.1.0", top_level=False))
        msg = str(cm.exception)
        self.assertIn("5.1.0", msg)
        self.assertIn("opencv-python-headless>=4.9,<5", msg)

    def test_missing_data_dir(self):
        with self.assertRaises(vision.DetectorUnavailable):
            vision.load_face_cascade(fake_cv2(data=False))

    def test_missing_hog(self):
        with self.assertRaises(vision.DetectorUnavailable):
            vision.load_people_detector(fake_cv2(hog=False))

    def test_check_passes_on_good_install(self):
        ok, lines = vision.check_features(fake_cv2())
        self.assertTrue(ok, lines)

    def test_check_fails_on_opencv5(self):
        ok, lines = vision.check_features(fake_cv2("5.1.0", top_level=False))
        self.assertFalse(ok)
        self.assertTrue(any("FAIL" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
