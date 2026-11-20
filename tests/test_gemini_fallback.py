import unittest
import urllib.error

from clip_engine import gemini

LIST2 = {"models": [
    {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
]}
OK = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}


class T(unittest.TestCase):
    def test_rank_models_order(self):
        names = ["models/gemini-3.6-flash-lite", "models/gemini-3.8-flash",
                 "models/gemini-3.6-flash", "models/gemini-3.8-flash-live"]
        self.assertEqual(gemini.rank_models(names),
                         ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.6-flash-lite"])

    def test_stalled_model_falls_through_to_next(self):
        used = []

        def op(url, body=None, timeout=0):
            if "/models?" in url:
                return LIST2
            used.append(url.split("/models/")[1].split(":")[0])
            if "3.8" in url:
                raise TimeoutError()
            return OK
        out = gemini.generate("hi", key="k", retries=2, opener=op, sleep=lambda s: None)
        self.assertEqual(out, "ok")
        self.assertEqual(used, ["gemini-3.8-flash", "gemini-3.8-flash", "gemini-3.6-flash"])

    def test_auth_error_is_not_retried(self):
        calls = []

        def op(url, body=None, timeout=0):
            if "/models?" in url:
                return LIST2
            calls.append(1)
            raise urllib.error.HTTPError(url, 403, "denied", {}, None)
        with self.assertRaises(gemini.GeminiUnavailable):
            gemini.generate("hi", key="k", opener=op, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_probe_reports_timeout_without_url(self):
        def op(url, body=None, timeout=0):
            raise TimeoutError()
        ok, detail, secs = gemini.probe("SECRETKEY", "m", opener=op)
        self.assertFalse(ok)
        self.assertEqual(detail, "TimeoutError")
        self.assertNotIn("SECRETKEY", detail)

    def test_503_moves_on_and_aliases_are_last_resort(self):
        used = []

        def op(url, body=None, timeout=0):
            if "/models?" in url:
                return LIST2
            m = url.split("/models/")[1].split(":")[0]
            used.append(m)
            if m != "gemini-flash-latest":
                raise urllib.error.HTTPError(url, 503, "busy", {}, None)
            return OK
        out = gemini.generate("hi", key="k", retries=1, opener=op, sleep=lambda s: None)
        self.assertEqual(out, "ok")
        self.assertEqual(used, ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-flash-latest"])

    def test_total_call_cap_stops_the_chain(self):
        calls = []

        def op(url, body=None, timeout=0):
            if "/models?" in url:
                return LIST2
            calls.append(1)
            raise urllib.error.HTTPError(url, 503, "busy", {}, None)
        with self.assertRaises(gemini.GeminiUnavailable) as cm:
            gemini.generate("hi", key="k", retries=2, opener=op, sleep=lambda s: None,
                            max_calls=3)
        self.assertEqual(len(calls), 3)
        self.assertIn("call cap", str(cm.exception))

    def test_deadline_stops_the_chain(self):
        t = [0.0]

        def op(url, body=None, timeout=0):
            if "/models?" in url:
                return LIST2
            t[0] += 40
            raise TimeoutError()
        with self.assertRaises(gemini.GeminiUnavailable) as cm:
            gemini.generate("hi", key="k", retries=2, opener=op, sleep=lambda s: None,
                            deadline=75, clock=lambda: t[0])
        self.assertIn("deadline", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
