import json
import unittest

from clip_engine import find, gemini, rank_local as rl


def W(start, heat, text="", speech=0.6, peak=-8.0, mean=-20.0, motion=0.0):
    return rl.Window(start, start + 35, text, speech, peak, mean, motion_lift=motion, heat=heat,
                     peak_t=start + 20)


def fake_gen(replies):
    seen = []

    def gen(prompt, **kw):
        seen.append((prompt, kw))
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    gen.seen = seen
    return gen


class T(unittest.TestCase):
    def test_parse_json_in_fences(self):
        self.assertEqual(find.parse_json('```json\n[{"id": 1}]\n```'), [{"id": 1}])
        with self.assertRaises(ValueError):
            find.parse_json("no json here")

    def test_images_go_in_the_request_body(self):
        seen = {}

        def op(url, body=None, timeout=0):
            if "/models?" in url:
                return {"models": [{"name": "models/gemini-3.8-flash",
                                    "supportedGenerationMethods": ["generateContent"]}]}
            seen["parts"] = body["contents"][0]["parts"]
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        gemini.generate("look", key="k", opener=op, images=["QUJD", "REVG"])
        self.assertEqual(seen["parts"][0], {"text": "look"})
        self.assertEqual(seen["parts"][1]["inline_data"], {"mime_type": "image/jpeg", "data": "QUJD"})
        self.assertEqual(len(seen["parts"]), 3)

    def test_full_pipeline_with_fake_gemini(self):
        wins = [W(10, 5.0, "no way he did it"), W(80, 3.0, "", speech=0.02, peak=-6, mean=-20, motion=3.0),
                W(150, 1.0, "just talking", speech=0.7, peak=-18, mean=-20)]
        gen = fake_gen([
            json.dumps([{"id": 0, "score": 8, "reason": "big reaction", "hook": "NO WAY"},
                        {"id": 1, "score": 7, "reason": "silent crash", "hook": ""},
                        {"id": 2, "score": 2, "reason": "flat", "hook": ""}]),
            json.dumps({"watch_score": 9, "what": "car flips", "facecam": "bottom-left", "problems": "none"}),
            json.dumps({"watch_score": 5, "what": "chase", "facecam": "none", "problems": "dark"}),
        ])
        res = find.evaluate(wins, 1200, gen, frames_fn=lambda c: ["QQ=="], top_watch=2)
        self.assertEqual([c["gate_ok"] for c in res], [True, True, False])  # flat one gated out, last
        self.assertEqual(res[0]["id"], 0)
        self.assertEqual(res[0]["stream_start"], 1210.0)
        self.assertEqual(res[0]["watch"]["facecam"], "bottom-left")
        self.assertIn("action", res[1]["gate"])      # voiceless crash passes on the action path
        self.assertIsNone(res[2]["watch"])
        txt = find.report(res, 1200)
        self.assertIn("stream 1210-1245s", txt)
        self.assertIn("hook idea: NO WAY", txt)

    def test_gemini_down_falls_back_to_local_and_says_so(self):
        wins = [W(10, 5.0, "no way")]
        logs = []
        res = find.evaluate(wins, 0, fake_gen([gemini.GeminiUnavailable("503")]),
                            frames_fn=lambda c: ["QQ=="], log=logs.append)
        self.assertIn("gemini scoring unavailable", res[0]["notes"])
        self.assertIsNone(res[0]["gemini"])
        self.assertEqual(res[0]["combined"], res[0]["local_score10"])   # missing stage is not a zero
        self.assertTrue(any("unavailable" in x for x in logs))

    def test_garbage_reply_is_handled_not_faked(self):
        res = find.evaluate([W(10, 5.0, "no way")], 0, fake_gen(["sorry, cannot"]), log=lambda s: None)
        self.assertIsNone(res[0]["gemini"])

    def test_combine_weights(self):
        c = {"local_score10": 5.0, "gemini": {"score": 10.0}, "watch": {"score": 0.0}}
        self.assertAlmostEqual(find.combine(c), 0.3 * 5 + 0.4 * 10 + 0.3 * 0, places=2)
        self.assertEqual(find.combine({"local_score10": 4.0, "gemini": None, "watch": None}), 4.0)

    def test_frame_cmd_seeks_before_input(self):
        cmd = find.frame_cmd("m.mp4", 12.5)
        self.assertLess(cmd.index("-ss"), cmd.index("-i"))
        self.assertIn("scale=480:-2", cmd)


if __name__ == "__main__":
    unittest.main()
