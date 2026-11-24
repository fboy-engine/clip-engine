"""Instagram Graph API Reels uploader (resumable upload: no public video URL is needed).

Two modes, ONE switch:
  - dry-run (default): plan() builds the exact requests and returns them as redacted log lines. Nothing is sent.
  - real: only for the ONE clip the Campaign cell real_upload names ("ONE:<n>"); the runner clears the cell
    before the upload, so the switch can never post a second clip.

Requests (Graph API, v21.0):
  1. POST /<IG_USER_ID>/media  media_type=REELS upload_type=resumable caption share_to_feed  -> container id
  2. POST rupload.facebook.com/ig-api-upload/<ver>/<container>  (offset 0, file_size, body = the mp4)
  3. GET  /<container>?fields=status_code  until FINISHED (ERROR / EXPIRED = refused)
  4. POST /<IG_USER_ID>/media_publish  creation_id=<container>  -> media id
  5. GET  /<media id>?fields=permalink
Error rules (they decide what poster.py does next):
  steps 1-3 fail  -> UploadRejected (nothing is published, tick APPROVE again)
  step 4 4xx      -> UploadRejected (the API said no)
  step 4 network/5xx/timeout, step 5 fails -> UploadAmbiguous (it may be live: poster.reconcile() uses find())
The token goes only in an Authorization header, never in a URL, a log line or an error text.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlencode

from . import poster, safelog

API_VERSION = "v21.0"
GRAPH = "https://graph.facebook.com/" + API_VERSION
RUPLOAD = "https://rupload.facebook.com/ig-api-upload/" + API_VERSION
POLL_EVERY_S = 5
POLL_MAX_S = 300
MAX_BYTES = 300 * 1024 * 1024
FIND_SLACK_S = 120
_SWITCH = re.compile(r"^ONE:(\d+)$")


def parse_switch(raw):
    """Campaign cell real_upload -> the clip number allowed to post for real, or None."""
    m = _SWITCH.match(str(raw or "").strip().upper())
    return int(m.group(1)) if m else None


def switch_allows(raw, n):
    return parse_switch(raw) == n


def caption_digest(caption):
    return "%d chars sha256:%s" % (len(caption), hashlib.sha256(caption.encode("utf-8")).hexdigest()[:8])


def build_requests(caption, file_size):
    """The exact requests of one post, token and user id as placeholders. Pure."""
    return [
        {"step": "1 create container", "method": "POST", "url": "%s/{IG_USER_ID}/media" % GRAPH,
         "form": {"media_type": "REELS", "upload_type": "resumable", "share_to_feed": "true", "caption": caption}},
        {"step": "2 upload bytes", "method": "POST", "url": "%s/{CONTAINER_ID}" % RUPLOAD,
         "headers": {"offset": "0", "file_size": str(file_size)}},
        {"step": "3 wait for FINISHED", "method": "GET", "url": "%s/{CONTAINER_ID}?fields=status_code" % GRAPH},
        {"step": "4 publish", "method": "POST", "url": "%s/{IG_USER_ID}/media_publish" % GRAPH,
         "form": {"creation_id": "{CONTAINER_ID}"}},
        {"step": "5 permalink", "method": "GET", "url": "%s/{MEDIA_ID}?fields=permalink" % GRAPH},
    ]


def plan_lines(caption, file_size):
    """Redacted text lines of build_requests (public-repo safe: caption as length + hash, auth as ***)."""
    lines = []
    for r in build_requests(caption, file_size):
        form = dict(r.get("form") or {})
        if "caption" in form:
            form["caption"] = "<%s>" % caption_digest(caption)
        parts = [r["step"] + ":", r["method"], r["url"]]
        parts += ["%s=%s" % kv for kv in form.items()]
        parts += ["%s=%s" % kv for kv in (r.get("headers") or {}).items()]
        parts.append("Authorization=***")
        lines.append(" ".join(parts))
    return lines


def _http(method, url, headers=None, body=None, timeout=60):
    """Default transport. Returns (status, parsed json or {}). Network problems raise OSError."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        return status, {}


def _form(d):
    return urlencode(d).encode("utf-8")


FORM = {"Content-Type": "application/x-www-form-urlencoded"}


class InstagramUploader:
    """real=False -> post() refuses (dry-run); plan() and find() still work. media(item) must set item.file."""

    def __init__(self, token, user_id, real=False, http=None, sleep=None, clock=None, media=None):
        self.token, self.user_id, self.real = token or "", str(user_id or ""), real
        self.http = http or _http
        self.sleep = sleep or time.sleep
        self.clock = clock or time.monotonic
        self.media = media
        self.secrets = [s for s in (self.token, self.user_id) if len(s) >= 6]

    def _safe(self, e):
        return safelog.redact("%s: %s" % (type(e).__name__, e), self.secrets).replace("\n", " ")[:160]

    def _err(self, data):
        e = (data or {}).get("error") or {}
        return safelog.redact("code=%s sub=%s %s" % (e.get("code"), e.get("error_subcode"), e.get("message", "")),
                              self.secrets).replace("\n", " ")[:160]

    def _call(self, method, url, headers=None, body=None, timeout=60):
        h = {"Authorization": "Bearer " + self.token}
        h.update(headers or {})
        return self.http(method, url, h, body, timeout)

    def _file(self, item):
        if not item.file and self.media:
            self.media(item)
        if not item.file or not os.path.isfile(item.file):
            raise poster.UploadRejected("clip file is not available")
        size = os.path.getsize(item.file)
        if size <= 0 or size > MAX_BYTES:
            raise poster.UploadRejected("clip file size %d is not allowed" % size)
        return size

    def plan(self, item):
        """Dry-run: the exact requests as log lines. Never sends anything."""
        try:
            size = self._file(item)
        except poster.UploadRejected as e:
            return ["plan: %s (file_size unknown)" % e] + plan_lines(item.caption, "unknown")
        return plan_lines(item.caption, size)

    def post(self, item, token):
        if not self.real:
            raise poster.UploadRejected("dry-run: the real upload switch is off")
        if not self.token or not self.user_id:
            raise poster.UploadRejected("Instagram secrets are not set")
        from . import caption as capmod, config
        try:
            capmod.verify_caption(config.load(), item.caption)
        except capmod.CaptionError as e:
            raise poster.UploadRejected("caption check: %s" % e)
        size = self._file(item)
        base = "%s/%s" % (GRAPH, self.user_id)
        try:
            st, d = self._call("POST", base + "/media", FORM,
                               _form({"media_type": "REELS", "upload_type": "resumable", "share_to_feed": "true",
                                      "caption": item.caption}))
            cid = (d or {}).get("id")
            if st != 200 or not cid:
                raise poster.UploadRejected("create container HTTP %s %s" % (st, self._err(d)))
            with open(item.file, "rb") as f:
                data = f.read()
            st, d = self._call("POST", "%s/%s" % (RUPLOAD, cid), {"offset": "0", "file_size": str(size)}, data, 600)
            if st != 200 or not (d or {}).get("success", True):
                raise poster.UploadRejected("upload bytes HTTP %s %s" % (st, self._err(d)))
            end = self.clock() + POLL_MAX_S
            while True:
                st, d = self._call("GET", "%s/%s?fields=status_code" % (GRAPH, cid))
                code = (d or {}).get("status_code")
                if code == "FINISHED":
                    break
                if code in ("ERROR", "EXPIRED") or (st != 200 and st < 500):
                    raise poster.UploadRejected("container %s HTTP %s %s" % (code or "?", st, self._err(d)))
                if self.clock() >= end:
                    raise poster.UploadRejected("container not FINISHED after %d s" % POLL_MAX_S)
                self.sleep(POLL_EVERY_S)
        except poster.UploadRejected:
            raise
        except Exception as e:  # before publish nothing is live, so even a timeout is a clean refusal
            raise poster.UploadRejected("before publish: " + self._safe(e))
        # step 4: from here on a failure may still have published
        try:
            st, d = self._call("POST", base + "/media_publish", FORM, _form({"creation_id": cid}))
        except Exception as e:
            raise poster.UploadAmbiguous("publish: " + self._safe(e))
        mid = (d or {}).get("id")
        if st >= 500 or st in (408, 429):
            raise poster.UploadAmbiguous("publish HTTP %s" % st)
        if st != 200 or not mid:
            raise poster.UploadRejected("publish HTTP %s %s" % (st, self._err(d)))
        try:
            st, d = self._call("GET", "%s/%s?fields=permalink" % (GRAPH, mid))
            link = (d or {}).get("permalink")
        except Exception as e:
            raise poster.UploadAmbiguous("permalink: " + self._safe(e))
        if st != 200 or not link:
            raise poster.UploadAmbiguous("posted as %s but no permalink yet (reconcile will read it)" % mid)
        return str(mid), link

    def find(self, item, token, since):
        """Read-only: did a Reel with this exact caption appear since the intent time? (media id, permalink) or None."""
        if not self.token or not self.user_id:
            return None
        st, d = self._call("GET", "%s/%s/media?fields=id,caption,permalink,timestamp&limit=15" % (GRAPH, self.user_id))
        if st != 200:
            raise RuntimeError("media list HTTP %s" % st)
        want = " ".join((item.caption or "").split())
        for m in (d or {}).get("data", []):
            if " ".join(str(m.get("caption") or "").split()) != want:
                continue
            ts = _parse_ts(m.get("timestamp"))
            if since is not None and ts is not None and (ts - since).total_seconds() < -FIND_SLACK_S:
                continue
            if m.get("id") and m.get("permalink"):
                return str(m["id"]), m["permalink"]
        return None


def _parse_ts(s):
    try:
        return datetime.strptime(str(s), "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)
    except ValueError:
        return None
