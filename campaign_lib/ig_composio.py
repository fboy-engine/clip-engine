"""Instagram Reel upload through the user's Composio Instagram connection (no Graph token in this repo).

Same interface as instagram.InstagramUploader: plan(item), post(item, token) -> (media_id, permalink).
real=False refuses to post (UploadRejected). Before the publish call fails -> UploadRejected (nothing is live).
The publish call failing, or no permalink afterwards, may mean it is live -> UploadAmbiguous (never retried)."""
import os
import re
import time
import urllib.error
import urllib.request

from . import caption as capmod, composio, config, poster

CREATE, PUBLISH = "INSTAGRAM_POST_IG_USER_MEDIA", "INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH"
WHOAMI, GET_MEDIA = "INSTAGRAM_GET_USER_INFO", "INSTAGRAM_GET_IG_MEDIA"
PERMALINK = re.compile(r"^https://(www\.)?instagram\.com/\S+$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def resolve_url(url):
    """Meta must fetch a direct file URL: follow the GitHub release redirect once and return the final URL."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(urllib.request.Request(url, headers={"User-Agent": "campaign-lib/1.0"}), timeout=60).close()
        return url
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
            return e.headers["Location"]
        raise


def release_url(item):
    name = item.source.split("pool:", 1)[-1]
    repo = os.environ.get("GITHUB_REPOSITORY", "fboy-engine/clip-engine")
    return "https://github.com/%s/releases/download/pool/%s" % (repo, name)


class ComposioInstagramUploader:
    def __init__(self, client=None, real=False, resolve=resolve_url, sleep=time.sleep):
        self.client = client or composio.Client(toolkit="instagram")
        self.real, self.resolve, self.sleep = real, resolve, sleep

    def plan(self, item):
        return ["composio %s ig_user_id=<me> media_type=REELS video_url=%s caption=%d chars" % (CREATE, release_url(item), len(item.caption)),
                "composio %s creation_id=<container> (waits for processing)" % PUBLISH]

    def post(self, item, token):
        if not self.real:
            raise poster.UploadRejected("dry-run: the real upload switch is off")
        try:
            capmod.verify_caption(config.load(), item.caption)
            url = self.resolve(release_url(item))
            uid = str(composio.find_key(self.client.execute(WHOAMI, {"ig_user_id": "me", "fields": "id,username"}), "id") or "")
            if not uid:
                raise poster.UploadRejected("could not read the Instagram account id")
            cid = composio.find_key(self.client.execute(CREATE, {
                "ig_user_id": uid, "video_url": url, "caption": item.caption, "media_type": "REELS", "share_to_feed": True}), "id")
            if not cid:
                raise poster.UploadRejected("no container id")
        except poster.UploadRejected:
            raise
        except Exception as e:                      # nothing is published before the publish call
            raise poster.UploadRejected("before publish: %s: %s" % (type(e).__name__, str(e)[:100]))
        try:
            mid = composio.find_key(self.client.execute(PUBLISH, {"ig_user_id": uid, "creation_id": str(cid), "max_wait_seconds": 300}), "id")
        except Exception as e:
            raise poster.UploadAmbiguous("publish: %s: %s" % (type(e).__name__, str(e)[:100]))
        if not mid:
            raise poster.UploadAmbiguous("publish gave no media id")
        for _ in range(4):
            try:
                link = composio.find_key(self.client.execute(GET_MEDIA, {"ig_media_id": str(mid), "fields": "id,permalink"}), "permalink")
                if link and PERMALINK.match(str(link)):
                    return str(mid), str(link)
            except Exception:
                pass
            self.sleep(5)
        raise poster.UploadAmbiguous("published (media id %s) but no permalink yet" % mid)
