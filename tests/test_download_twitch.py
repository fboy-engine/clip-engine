"""A3-3: Twitch clip download: anonymous, whole clip, never a portrait format (fake yt-dlp runner, nothing is downloaded)."""
import os
import tempfile
import unittest

from clip_engine import download

URL = "https://m.twitch.tv/laurenhamdenn/clip/GoldenSincereHamburgerHeyGuys-Zo31y1z1tLdwRScS"


class TwitchDownload(unittest.TestCase):
    def test_is_twitch(self):
        for u in (URL, "https://www.twitch.tv/x/clip/Y", "https://clips.twitch.tv/Y", "http://twitch.tv/videos/1"):
            self.assertTrue(download.is_twitch(u), u)
        for u in ("https://www.youtube.com/watch?v=LHoHInbPkk0", "", None, "https://nottwitch.tv.example.com/x"):
            self.assertFalse(download.is_twitch(u), u)

    def test_vod_is_not_a_clip(self):
        # run 45591019498: the VOD went down the clip path with clip format ids and failed "Requested format is not available"
        for u in ("https://m.twitch.tv/videos/2934045907", "https://www.twitch.tv/videos/2934045907?t=1h", "http://twitch.tv/v/12"):
            self.assertTrue(download.is_twitch(u), u)
            self.assertTrue(download.is_twitch_vod(u), u)
            self.assertFalse(download.is_twitch_clip(u), u)
        for u in (URL, "https://clips.twitch.tv/Y", "https://www.twitch.tv/x/clip/Y"):
            self.assertTrue(download.is_twitch_clip(u), u)
            self.assertFalse(download.is_twitch_vod(u), u)

    def test_vod_uses_the_section_download(self):
        seen = []

        def runner(cmd, timeout):
            seen.append(cmd)
            with open(os.path.join(d, "source.mp4"), "wb") as f:
                f.write(b"x")
            return 0, ""
        with tempfile.TemporaryDirectory() as d:
            download.fetch_window("https://m.twitch.tv/videos/2934045907", 300, 900, d, runner=runner)
        cmd = seen[0]
        self.assertIn("--download-sections", cmd)
        self.assertEqual(cmd[cmd.index("--download-sections") + 1], "*300-900")
        self.assertNotEqual(cmd[cmd.index("-f") + 1], "1080/720/480/360")

    def test_command_is_anonymous_whole_clip_and_landscape_only(self):
        seen = []

        def runner(cmd, timeout):
            seen.append(cmd)
            with open(os.path.join(d, "source.mp4"), "wb") as f:
                f.write(b"x")
            return 0, ""
        with tempfile.TemporaryDirectory() as d:
            os.environ["YT_COOKIES_FILE"], os.environ["YT_PROXY"] = "/tmp/never.txt", "socks5://never"
            try:
                path = download.fetch_twitch_clip(URL, d, runner=runner)
            finally:
                del os.environ["YT_COOKIES_FILE"], os.environ["YT_PROXY"]
            self.assertEqual(path, os.path.join(d, "source.mp4"))
        cmd = seen[0]
        self.assertEqual(len(seen), 1)
        for bad in ("--cookies", "--proxy", "--extractor-args", "--download-sections", "--force-keyframes-at-cuts"):
            self.assertNotIn(bad, cmd)
        self.assertEqual(cmd[cmd.index("-f") + 1], "1080/720/480/360")
        self.assertNotIn("portrait", " ".join(cmd))
        self.assertEqual(cmd[-1], URL)

    def test_two_attempts_then_a_clear_error(self):
        calls = []

        def runner(cmd, timeout):
            calls.append(1)
            return 1, "ERROR: boom"
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(download.DownloadFailed) as cm:
                download.fetch_twitch_clip(URL, d, runner=runner)
        self.assertEqual(len(calls), 2)
        self.assertIn("boom", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
