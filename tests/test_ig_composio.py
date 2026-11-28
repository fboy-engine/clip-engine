import unittest

from campaign_lib import config, ig_composio, poster, states


class Fake:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def execute(self, slug, args):
        self.calls.append((slug, args))
        if slug == self.fail:
            raise RuntimeError("boom")
        return {"id": "123"} if slug != ig_composio.GET_MEDIA else {"id": "9", "permalink": "https://www.instagram.com/reel/ABC/"}


def item():
    return states.Item(n=1, source="pool:clip-1-x.mp4", kind="pool", caption=__import__("campaign_lib.caption", fromlist=["x"]).build_caption(config.load(), "Lauren Hamden stream moment"))


class IgComposio(unittest.TestCase):
    def test_dry_run_refuses(self):
        u = ig_composio.ComposioInstagramUploader(Fake(), real=False)
        with self.assertRaises(poster.UploadRejected):
            u.post(item(), "t")

    def test_real_post_order_and_link(self):
        f = Fake()
        u = ig_composio.ComposioInstagramUploader(f, real=True, resolve=lambda x: "https://cdn/x.mp4?sig=1", sleep=lambda s: None)
        mid, link = u.post(item(), "t")
        self.assertEqual([c[0] for c in f.calls], [ig_composio.WHOAMI, ig_composio.CREATE, ig_composio.PUBLISH, ig_composio.GET_MEDIA])
        self.assertEqual(f.calls[1][1]["video_url"], "https://cdn/x.mp4?sig=1")
        self.assertEqual(link, "https://www.instagram.com/reel/ABC/")

    def test_create_failure_is_rejected_publish_failure_ambiguous(self):
        u = ig_composio.ComposioInstagramUploader(Fake(fail=ig_composio.CREATE), real=True, resolve=lambda x: x, sleep=lambda s: None)
        with self.assertRaises(poster.UploadRejected):
            u.post(item(), "t")
        u = ig_composio.ComposioInstagramUploader(Fake(fail=ig_composio.PUBLISH), real=True, resolve=lambda x: x, sleep=lambda s: None)
        with self.assertRaises(poster.UploadAmbiguous):
            u.post(item(), "t")


if __name__ == "__main__":
    unittest.main()
