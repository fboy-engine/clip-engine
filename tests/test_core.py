import os, unittest, urllib.error
from clip_engine import gemini, rank_local as rl, cropplan as cp, captions as cap, config

LIST = {"models": [{"name": "models/gemini-3.8-flash",
                    "supportedGenerationMethods": ["generateContent"]}]}


class T(unittest.TestCase):
    def test_pick_model_newest_flash(self):
        names = ["models/gemini-2.5-flash", "models/gemini-3.6-flash",
                 "models/gemini-3.8-flash", "models/gemini-3.8-flash-lite",
                 "models/gemini-3.8-flash-live"]
        self.assertEqual(gemini.pick_model(names), "gemini-3.8-flash")

    def test_pick_model_none(self):
        self.assertIsNone(gemini.pick_model(["models/embedding-001"]))

    def test_404_raises_unavailable(self):
        def op(url, body=None, timeout=0):
            if "/models?" in url:
                return LIST
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        with self.assertRaises(gemini.GeminiUnavailable):
            gemini.generate("hi", key="k", opener=op, sleep=lambda s: None)

    def test_timeout_retries_then_gives_up(self):
        calls = []
        def op(url, body=None, timeout=0):
            if "/models?" in url:
                return LIST
            calls.append(1)
            raise TimeoutError()
        with self.assertRaises(gemini.GeminiUnavailable):
            gemini.generate("hi", key="k", retries=3, opener=op, sleep=lambda s: None)
        self.assertEqual(len(calls), gemini.MAX_CALLS)  # total cap, not retries x chain

    def test_retry_then_success(self):
        n = {"i": 0}
        def op(url, body=None, timeout=0):
            if "/models?" in url:
                return LIST
            n["i"] += 1
            if n["i"] < 3:
                raise TimeoutError()
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        self.assertEqual(gemini.generate("hi", key="k", opener=op, sleep=lambda s: None), "ok")

    def test_no_key(self):
        old = os.environ.pop("GEMINI_API_KEY", None)
        try:
            with self.assertRaises(gemini.GeminiUnavailable):
                gemini.generate("x")
        finally:
            if old is not None:
                os.environ["GEMINI_API_KEY"] = old

    def test_prompt_truncated(self):
        seen = {}
        def op(url, body=None, timeout=0):
            if "/models?" in url:
                return LIST
            seen["n"] = len(body["contents"][0]["parts"][0]["text"])
            return {"candidates": [{"content": {"parts": [{"text": "x"}]}}]}
        gemini.generate("a" * 50000, key="k", opener=op, sleep=lambda s: None)
        self.assertEqual(seen["n"], 6000)

    def test_payoff_gate(self):
        good = rl.Window(0, 35, "no way he actually did it", 0.6, -8, -20)
        flat = rl.Window(0, 35, "no way", 0.6, -18, -20)
        action = rl.Window(0, 35, "", 0.05, -6, -20)
        short = rl.Window(0, 10, "no way", 0.6, -8, -20)
        self.assertTrue(rl.payoff_gate(good)[0])
        self.assertFalse(rl.payoff_gate(flat)[0])
        self.assertFalse(rl.payoff_gate(action)[0])
        self.assertFalse(rl.payoff_gate(short)[0])
        self.assertEqual(rl.rank([good, flat, action, short]), [good])

    def test_punch_caps(self):
        self.assertLessEqual(max(cp.punch_curve(100, peak=0.5)), config.PUNCH_TARGET)
        _, _, h = cp.crop_window(0.5, 1920, 1080, zoom=0.9)
        self.assertAlmostEqual(h, 1080 / 1.12, places=3)

    def test_crop_in_frame_and_ratio(self):
        for cx in (0.0, 0.5, 1.0):
            x, w, h = cp.crop_window(cx, 1920, 1080)
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x + w, 1920 + 1e-6)
            self.assertAlmostEqual(w / h, 9 / 16, places=4)

    def test_smooth_holds_on_jitter(self):
        self.assertEqual(len(set(cp.smooth([0.5, 0.505, 0.495, 0.5]))), 1)

    def test_captions_one_highlight_per_line(self):
        words = [{"w": w, "s": i * .3, "e": i * .3 + .25}
                 for i, w in enumerate("no way he actually landed it".split())]
        ass = cap.to_ass(words)
        rows = [l for l in ass.splitlines() if l.startswith("Dialogue")]
        self.assertTrue(rows)
        for line in rows:
            self.assertLessEqual(line.count(cap.HL_ON), 1)
        self.assertIn("actually", ass)


if __name__ == "__main__":
    unittest.main()
