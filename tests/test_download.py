import os
import tempfile
import unittest

from clip_engine import download as dl


class T(unittest.TestCase):
    def test_redacts_googlevideo_url(self):
        s = "GET https://rr3---sn-abc.googlevideo.com/videoplayback?expire=1&sig=AAA&ip=1.2.3.4 ok"
        out = dl.redact(s)
        self.assertNotIn("googlevideo", out)
        self.assertNotIn("sig=AAA", out)

    def test_redacts_cookie_header_and_names(self):
        self.assertEqual(dl.redact("Cookie: SID=abc; HSID=def"), "Cookie: <redacted>")
        out = dl.redact("found SAPISID=xyz123 in jar")
        self.assertNotIn("xyz123", out)

    def test_redacts_netscape_cookie_line(self):
        line = ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tg.a000secretvalue"
        out = dl.redact("loaded " + line)
        self.assertNotIn("secretvalue", out)

    def test_redacts_long_tokens_only(self):
        self.assertIn("<redacted-token>", dl.redact("tok " + "abc" * 20))
        ok = "[youtube] Extracting URL: https://www.youtube.com/watch?v=LF_7ae5z8DE"
        self.assertEqual(dl.redact(ok), ok)
        ok2 = "[download] Destination: out/source.f299.mp4"
        self.assertEqual(dl.redact(ok2), ok2)

    def test_common_args(self):
        a = dl.common_args("/t/c.txt", "http://127.0.0.1:8080", "tv")
        self.assertIn("--cookies", a)
        self.assertIn("http://127.0.0.1:8080", a)
        self.assertIn("youtube:player_client=tv", a)
        self.assertNotIn("--proxy", dl.common_args())

    def _fake(self, fail_first):
        calls = []

        def runner(cmd, timeout):
            calls.append(cmd)
            if len(calls) <= fail_first:
                return 1
            out = cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp4")
            open(out, "w").write("x")
            return 0
        return runner, calls

    def test_falls_back_to_next_client(self):
        with tempfile.TemporaryDirectory() as d:
            runner, calls = self._fake(1)
            path, client = dl.fetch_window("u", 10, 50, d, runner=runner)
            self.assertEqual(client, "tv")
            self.assertTrue(os.path.exists(path))
            self.assertEqual(len(calls), 2)

    def test_gives_up_after_three_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            runner, calls = self._fake(99)
            with self.assertRaises(dl.DownloadFailed):
                dl.fetch_window("u", 10, 50, d, clients=(None, "tv", "mweb", "web_safari"),
                                runner=runner)
            self.assertEqual(len(calls), 3)

    def test_cut_is_stream_copy_not_reencode(self):
        with tempfile.TemporaryDirectory() as d:
            runner, calls = self._fake(0)
            dl.fetch_window("u", 600, 660, d, runner=runner)
            self.assertNotIn("--force-keyframes-at-cuts", calls[0])
            self.assertIn("*600-660", calls[0])


class BotCheckTests(unittest.TestCase):
    BOT = (1, "ERROR: [youtube] x: Sign in to confirm you\u2019re not a bot")

    def _runner(self, script):
        calls = []

        def runner(cmd, timeout):
            calls.append(cmd)
            r = script[len(calls) - 1]
            if r == "ok":
                open(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp4"), "w").write("x")
                return 0, ""
            return r
        return runner, calls

    def test_one_backoff_retry_then_stop(self):
        slept = []
        with tempfile.TemporaryDirectory() as d:
            runner, calls = self._runner([self.BOT, self.BOT, "ok"])
            with self.assertRaises(dl.BotCheck):
                dl.fetch_window("u", 1, 9, d, runner=runner, sleep=slept.append, backoff=7)
            self.assertEqual(len(calls), 2)       # never a third attempt
            self.assertEqual(slept, [7])          # exactly one backoff

    def test_retry_after_bot_check_can_succeed(self):
        with tempfile.TemporaryDirectory() as d:
            runner, calls = self._runner([self.BOT, "ok"])
            path, client = dl.fetch_window("u", 1, 9, d, runner=runner, sleep=lambda s: None)
            self.assertEqual(client, "tv")
            self.assertEqual(len(calls), 2)

    def test_other_errors_keep_the_client_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            runner, calls = self._runner([(1, "HTTP 403"), (1, "HTTP 403"), (1, "HTTP 403")])
            with self.assertRaises(dl.DownloadFailed) as cm:
                dl.fetch_window("u", 1, 9, d, runner=runner, sleep=lambda s: None)
            self.assertNotIsInstance(cm.exception, dl.BotCheck)
            self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
