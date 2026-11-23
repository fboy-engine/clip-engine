"""Caption sources: rescue of words the VAD pass missed, OCR line merging, Gemini audio text."""
import unittest

import numpy as np

from clip_engine import caption_sources as cs
from clip_engine import gemini


def W(text, t0, step=0.32, p=0.95):
    out, t = [], t0
    for x in text.split():
        out.append({"w": x, "s": round(t, 3), "e": round(t + step - 0.04, 3), "p": p})
        t += step
    return out


class Uncovered(unittest.TestCase):
    def test_finds_speech_without_a_word(self):
        words = W("we just got", 0.0) + W("vault", 2.0)
        ch = cs.uncovered(words, [(0.0, 2.4)])
        self.assertEqual(len(ch), 1)
        self.assertAlmostEqual(ch[0][0], 1.02, places=2)      # end of 'got' + 0.1 pad
        self.assertLess(ch[0][1], 2.0)

    def test_fully_covered_speech_has_no_chunk(self):
        self.assertEqual(cs.uncovered(W("we just got to the vault", 0.0), [(0.0, 1.9)]), [])

    def test_tiny_gaps_are_ignored(self):
        words = W("a b", 0.0) + W("c", 0.8)
        self.assertEqual(cs.uncovered(words, [(0.0, 1.1)]), [])


class Rescue(unittest.TestCase):
    def setUp(self):
        self.pcm = np.zeros(16000 * 5, dtype=np.float32)
        self.words = W("we just got", 0.0) + W("vault", 3.0)
        self.spans = [(0.0, 3.3)]

    def test_adds_a_word_the_first_pass_missed(self):
        ws = [{"w": "to", "s": 0.75, "e": 0.95, "p": 0.8}, {"w": "the", "s": 1.0, "e": 1.2, "p": 0.9}]
        out, info = cs.rescue(self.pcm, self.words, self.spans, lambda a: ws)
        self.assertEqual([w["w"] for w in out], ["to", "the"])
        self.assertTrue(all(w["rescued"] for w in out))
        self.assertEqual(info["added"], 2)

    def test_low_confidence_word_is_dropped_and_logged(self):
        out, info = cs.rescue(self.pcm, self.words, self.spans, lambda a: [{"w": "mumble", "s": 0.75, "e": 0.95, "p": 0.2}])
        self.assertEqual(out, [])
        self.assertIn("p=0.20", info["dropped"][0]["why"])

    def test_word_on_an_existing_word_is_dropped(self):
        out, _ = cs.rescue(self.pcm, self.words, self.spans, lambda a: [{"w": "got", "s": 0.1, "e": 0.2, "p": 0.9}])
        self.assertEqual(out, [])

    def test_silence_hallucination_on_a_short_chunk_is_dropped(self):
        words = W("a b c", 0.0) + W("d e f", 1.7)
        out, _ = cs.rescue(self.pcm, words, [(0.0, 2.6)], lambda a: [{"w": "Thank you.", "s": 0.5, "e": 0.8, "p": 0.9}])
        self.assertEqual(out, [])

    def test_nothing_to_rescue_never_calls_the_model(self):
        def boom(a):
            raise AssertionError("model must not run")
        out, info = cs.rescue(self.pcm, W("we just got to the vault", 0.0), [(0.0, 1.9)], boom)
        self.assertEqual((out, info["added"]), ([], 0))


class Ocr(unittest.TestCase):
    def test_parse_tsv_keeps_confident_lines(self):
        head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"

        def row(ln, c, t):
            return f"5\t1\t1\t1\t{ln}\t1\t0\t0\t10\t10\t{c}\t{t}"
        tsv = "\n".join([head, row(1, 91, "Hello"), row(1, 90, "there"), row(2, 20, "xq"), row(3, -1, "")])
        self.assertEqual(cs.parse_tsv(tsv), [("Hello there", 90.5)])

    def test_merge_lines_joins_repeats_and_drops_flicker(self):
        s = [(0.0, [("Get in the car", 90.0)]), (0.5, [("Get in the car", 92.0)]), (1.0, [("Get in the carr", 88.0)]),
             (1.5, []), (2.0, [("Noise", 40.0)]), (3.0, [("Go now", 70.0)]), (3.5, [("Go now", 72.0)])]
        out = cs.merge_lines(s)
        self.assertEqual([x["text"] for x in out], ["Get in the car", "Go now"])
        self.assertEqual(out[0]["s"], 0.0)
        self.assertAlmostEqual(out[0]["e"], 1.5)
        self.assertEqual(out[0]["samples"], 3)

    def test_merge_lines_accepts_a_single_very_sure_read(self):
        self.assertEqual(len(cs.merge_lines([(1.0, [("Stop right there", 93.0)])])), 1)

    def test_blank_zeros_the_avoid_rect_in_band_coordinates(self):
        g = np.full((100, 200), 255, dtype=np.uint8)
        out = cs.blank(g, [(50, 520, 90, 560)], (40, 500))     # rect lands at x10..50, y20..60 in the band
        self.assertEqual(int(out[30, 20]), 0)
        self.assertEqual(int(out[30, 100]), 255)
        self.assertEqual(int(g[30, 20]), 255)                   # the input is untouched

    def test_ocr_lines_with_injected_frames(self):
        frames = [np.zeros((40, 80), dtype=np.uint8) for _ in range(4)]
        reads = iter([[("Run to the vault", 90.0)]] * 3 + [[]])
        lines, info = cs.ocr_lines("x.mp4", 0, 2, 1920, 1080, frames=frames, ocr=lambda g: next(reads))
        self.assertTrue(info["available"])
        self.assertEqual(lines[0]["text"], "Run to the vault")

    def test_ocr_lines_without_tesseract_says_so(self):
        orig = cs.tesseract_available
        cs.tesseract_available = lambda which=None: False
        try:
            lines, info = cs.ocr_lines("x.mp4", 0, 2, 1920, 1080)
        finally:
            cs.tesseract_available = orig
        self.assertEqual(lines, [])
        self.assertFalse(info["available"])
        self.assertIn("tesseract", info["error"])

    def test_real_tesseract_reads_a_synthetic_subtitle(self):
        if not cs.tesseract_available():
            self.skipTest("tesseract not installed")
        import glob
        from PIL import Image, ImageDraw, ImageFont
        fonts = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True)
        if not fonts:
            self.skipTest("no ttf font")
        bg = (np.random.default_rng(1).random((380, 1500)) * 90 + 20).astype(np.uint8)
        im = Image.fromarray(bg)
        ImageDraw.Draw(im).text((300, 250), "Get in the car, we are leaving now", font=ImageFont.truetype(fonts[0], 34),
                                fill=255, stroke_width=2, stroke_fill=0)
        got = cs.tesseract_frame(np.array(im))
        self.assertTrue(got and "leaving now" in got[0][0].lower())


class GeminiText(unittest.TestCase):
    def test_clean_removes_markup_and_labels(self):
        self.assertEqual(cs.clean_gemini("```\nTranscript: we got [music] to the vault (laughs)\n```"), "we got to the vault")

    def test_sends_the_audio_as_inline_wav(self):
        seen = {}

        def gen(prompt, key=None, **kw):
            seen.update(kw)
            return "we just got to the vault"
        text, err = cs.gemini_text(np.zeros(1600, dtype=np.float32), "k", generate=gen)
        self.assertEqual(text, "we just got to the vault")
        self.assertIsNone(err)
        self.assertEqual(seen["audio"][0], "audio/wav")

    def test_unavailable_is_an_error_string_not_an_exception(self):
        def gen(*a, **k):
            raise gemini.GeminiUnavailable("GEMINI_API_KEY not set")
        text, err = cs.gemini_text(np.zeros(160, dtype=np.float32), None, generate=gen)
        self.assertIsNone(text)
        self.assertIn("not set", err)

    def test_generate_puts_audio_in_the_request_body(self):
        bodies = []

        def opener(url, body=None, timeout=60):
            if body is None:
                return {"models": [{"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]}]}
            bodies.append(body)
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        self.assertEqual(gemini.generate("p", key="k", opener=opener, audio=("audio/wav", "QUJD")), "ok")
        self.assertEqual(bodies[0]["contents"][0]["parts"][-1], {"inline_data": {"mime_type": "audio/wav", "data": "QUJD"}})
