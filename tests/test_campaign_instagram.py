"""Instagram uploader: fake transport only. No network, no real token."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from campaign_lib import instagram, poster
from campaign_lib.states import Item

TOKEN, UID = "EAAB-secret-token-123456", "17841400000000099"
CAP = "Short line\n\n#LaurenHamden @LaurenHamden #GTA6"


class Fake:
    """Scripted transport: answers by (method, url fragment)."""

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, method, url, headers=None, body=None, timeout=60):
        self.calls.append((method, url, dict(headers or {}), body))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def item(tmp, size=1000):
    p = os.path.join(tmp, "clip.mp4")
    with open(p, "wb") as f:
        f.write(b"x" * size)
    it = Item(n=3, source="s", state="POSTING", caption=CAP)
    it.file = p
    return it


def up(answers, real=True, **kw):
    f = Fake(answers)
    return instagram.InstagramUploader(TOKEN, UID, real=real, http=f, sleep=lambda s: None, **kw), f


OK = [(200, {"id": "C1"}), (200, {"success": True}), (200, {"status_code": "FINISHED"}),
      (200, {"id": "M1"}), (200, {"permalink": "https://www.instagram.com/reel/AbC/"})]


class SwitchTests(unittest.TestCase):
    def test_switch_names_exactly_one_clip(self):
        self.assertEqual(instagram.parse_switch("ONE:3"), 3)
        self.assertEqual(instagram.parse_switch(" one:12 "), 12)
        for bad in ("", None, "3", "ONE:", "ALL", "ONE:3,4", "TRUE", "ONE:-1"):
            self.assertIsNone(instagram.parse_switch(bad), bad)
        self.assertTrue(instagram.switch_allows("ONE:3", 3))
        self.assertFalse(instagram.switch_allows("ONE:3", 4))


class PlanTests(unittest.TestCase):
    def test_plan_shows_the_five_requests_without_secrets_or_caption(self):
        with tempfile.TemporaryDirectory() as tmp:
            u, f = up([], real=False)
            lines = u.plan(item(tmp, 4321))
            self.assertEqual(len(lines), 5)
            text = "\n".join(lines)
            self.assertIn("media_type=REELS", text)
            self.assertIn("upload_type=resumable", text)
            self.assertIn("file_size=4321", text)
            self.assertIn("media_publish", text)
            for secret in (TOKEN, UID, "Short line", "LaurenHamden"):
                self.assertNotIn(secret, text)
            self.assertEqual(f.calls, [])  # nothing was sent

    def test_plan_without_file_still_shows_the_request(self):
        u, _ = up([], real=False)
        lines = u.plan(Item(n=1, source="s", caption=CAP))
        self.assertIn("file size unknown", lines[0] + " ".join(lines[1:]).replace("file_size=unknown", "file size unknown"))

    def test_dry_run_post_is_refused_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            u, f = up(OK, real=False)
            with self.assertRaises(poster.UploadRejected):
                u.post(item(tmp), "t")
            self.assertEqual(f.calls, [])


class PostTests(unittest.TestCase):
    def test_happy_path_order_and_auth_header_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            u, f = up(OK)
            self.assertEqual(u.post(item(tmp), "t"), ("M1", "https://www.instagram.com/reel/AbC/"))
            self.assertEqual([c[0] for c in f.calls], ["POST", "POST", "GET", "POST", "GET"])
            self.assertIn("/media", f.calls[0][1])
            self.assertIn("rupload.facebook.com", f.calls[1][1])
            self.assertEqual(f.calls[1][2]["file_size"], "1000")
            self.assertIn("media_publish", f.calls[3][1])
            for c in f.calls:
                self.assertEqual(c[2]["Authorization"], "Bearer " + TOKEN)
                self.assertNotIn(TOKEN, c[1])

    def test_before_publish_failures_are_clean_refusals(self):
        bads = [[(400, {"error": {"code": 190, "message": "bad " + TOKEN}})],
                [(200, {"id": "C1"}), (500, {})],
                [(200, {"id": "C1"}), (200, {"success": True}), (200, {"status_code": "ERROR"})],
                [(200, {"id": "C1"}), OSError("timed out")]]
        for answers in bads:
            with tempfile.TemporaryDirectory() as tmp:
                u, f = up(answers)
                with self.assertRaises(poster.UploadRejected) as cm:
                    u.post(item(tmp), "t")
                self.assertNotIn(TOKEN, str(cm.exception))
                self.assertFalse(any("media_publish" in c[1] for c in f.calls))

    def test_container_never_finishing_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ticks = iter(range(0, 10000, 100))
            u, f = up([(200, {"id": "C1"}), (200, {"success": True})] + [(200, {"status_code": "IN_PROGRESS"})] * 50,
                      clock=lambda: next(ticks))
            with self.assertRaises(poster.UploadRejected):
                u.post(item(tmp), "t")

    def test_publish_that_may_have_gone_through_is_ambiguous_never_rejected(self):
        for last in (OSError("reset"), (500, {}), (504, {}), (429, {})):
            with tempfile.TemporaryDirectory() as tmp:
                u, f = up(OK[:3] + [last])
                with self.assertRaises(poster.UploadAmbiguous):
                    u.post(item(tmp), "t")

    def test_publish_api_error_is_a_definitive_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            u, _ = up(OK[:3] + [(400, {"error": {"code": 9007, "message": "not ready"}})])
            with self.assertRaises(poster.UploadRejected):
                u.post(item(tmp), "t")

    def test_posted_but_permalink_missing_is_ambiguous_so_reconcile_reads_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            u, _ = up(OK[:4] + [(500, {})])
            with self.assertRaises(poster.UploadAmbiguous):
                u.post(item(tmp), "t")

    def test_bad_caption_is_never_uploaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            it = item(tmp)
            it.caption = "no required tags here"
            u, f = up(OK)
            with self.assertRaises(poster.UploadRejected):
                u.post(it, "t")
            self.assertEqual(f.calls, [])

    def test_missing_file_and_missing_secrets_are_refused(self):
        u, f = up(OK)
        with self.assertRaises(poster.UploadRejected):
            u.post(Item(n=1, source="s", caption=CAP), "t")
        u2 = instagram.InstagramUploader("", "", real=True, http=f)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(poster.UploadRejected):
                u2.post(item(tmp), "t")
        self.assertEqual(f.calls, [])

    def test_media_hook_fetches_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = item(tmp)
            it = Item(n=3, source="s", caption=CAP)
            u, _ = up(OK, media=lambda i: setattr(i, "file", src.file))
            self.assertEqual(u.post(it, "t")[0], "M1")


class FindTests(unittest.TestCase):
    SINCE = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)

    def media(self, caption, when):
        return {"id": "M9", "caption": caption, "permalink": "https://www.instagram.com/reel/Zz/",
                "timestamp": when.strftime("%Y-%m-%dT%H:%M:%S+0000")}

    def test_find_matches_caption_and_time(self):
        it = Item(n=3, source="s", caption=CAP)
        u, f = up([(200, {"data": [self.media(CAP, self.SINCE + timedelta(seconds=30))]})])
        self.assertEqual(u.find(it, "t", self.SINCE), ("M9", "https://www.instagram.com/reel/Zz/"))
        self.assertNotIn(TOKEN, f.calls[0][1])

    def test_find_ignores_other_captions_and_old_posts(self):
        it = Item(n=3, source="s", caption=CAP)
        u, _ = up([(200, {"data": [self.media("other", self.SINCE), self.media(CAP, self.SINCE - timedelta(hours=3))]})])
        self.assertIsNone(u.find(it, "t", self.SINCE))

    def test_find_error_raises_so_reconcile_keeps_posting(self):
        u, _ = up([(500, {})])
        with self.assertRaises(RuntimeError):
            u.find(Item(n=3, source="s", caption=CAP), "t", self.SINCE)

    def test_find_without_secrets_is_none(self):
        u = instagram.InstagramUploader("", "")
        self.assertIsNone(u.find(Item(n=1, source="s"), "t", self.SINCE))


if __name__ == "__main__":
    unittest.main()
