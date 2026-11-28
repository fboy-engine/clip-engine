import unittest

from campaign_lib import config, poolpost


class FakeClient:
    def __init__(self, rows, switch=""):
        self.rows, self.switch, self.writes = rows, switch, []

    def execute(self, slug, args):
        if slug == "GOOGLESHEETS_BATCH_GET":
            return {"valueRanges": [{"values": self.rows}, {"values": [[self.switch]] if self.switch else []}]}
        self.writes.append(args["data"][0])
        return {}


class FakeUp:
    def __init__(self, token, user, real=False):
        self.real = real

    def plan(self, item):
        return ["POST /media caption %d chars" % len(item.caption)]

    def post(self, item, token):
        raise AssertionError("dry run must not post")


HEAD = ["file_id", "name", "state", "link", "posted_utc"]


class PoolPost(unittest.TestCase):
    def test_idle_when_nothing_approved(self):
        c = FakeClient([HEAD, ["a", "x.mp4", "POSTED", "l"]])
        self.assertEqual(poolpost.run(c, config.load(), {}, log=lambda s: None), "idle")
        self.assertEqual(c.writes, [])

    def test_dry_run_writes_fake_link_and_never_posts(self):
        c = FakeClient([HEAD, ["a", "clip-1-s0-w1.mp4", "APPROVED", ""]])
        out = poolpost.run(c, config.load(), {}, download=lambda n, d: "/tmp/" + n, uploader_cls=FakeUp, log=lambda s: None)
        self.assertEqual(out, "dryrun")
        self.assertEqual(c.writes[0]["values"][0][0], "POSTING")
        self.assertEqual(c.writes[-1]["range"], "Review!C2:D2")
        self.assertEqual(c.writes[-1]["values"][0][0], "POSTED")
        self.assertIn("instagram.com/reel/DRYRUN", c.writes[-1]["values"][0][1])

    def test_bad_name_fails(self):
        c = FakeClient([HEAD, ["a", "../x;rm.mp4", "APPROVED", ""]])
        self.assertEqual(poolpost.run(c, config.load(), {}, log=lambda s: None), "failed")
        self.assertEqual(c.writes[-1]["values"][0][0], "FAILED")


if __name__ == "__main__":
    unittest.main()
