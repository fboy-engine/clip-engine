"""Minimal Composio v3 REST client (stdlib only). The runner uses it to read/write the Google Sheet
through the user's connected Google Sheets account. The API key comes from the COMPOSIO_API_KEY
env var and is never printed."""
import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("COMPOSIO_BASE", "https://backend.composio.dev/api/v3")


class ComposioError(RuntimeError):
    pass


def find_key(obj, key):
    """First value stored under `key` anywhere in a nested dict/list (Composio wraps answers)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


class Client:
    def __init__(self, api_key=None, account_id=None, user_id=None, toolkit="googlesheets"):
        self.api_key = api_key or os.environ.get("COMPOSIO_API_KEY", "")
        if not self.api_key:
            raise ComposioError("COMPOSIO_API_KEY is NOT SET")
        self.account_id = account_id or os.environ.get("COMPOSIO_CONNECTED_ACCOUNT_ID") or None
        self.user_id = user_id
        self.toolkit = toolkit

    def _call(self, method, path, body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        for attempt in (1, 2):
            req = urllib.request.Request(
                BASE + path, data=data, method=method,
                headers={"x-api-key": self.api_key, "Content-Type": "application/json",
                         "Accept": "application/json", "User-Agent": "campaign-lib/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read().decode() or "{}")
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace").replace(self.api_key, "***")[:500]
                if attempt == 1 and e.code in (429, 500, 502, 503, 504):
                    time.sleep(3)
                    continue
                raise ComposioError("HTTP %s on %s %s: %s" % (e.code, method, path.split("?")[0], text))
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == 1:
                    time.sleep(3)
                    continue
                raise ComposioError("network error on %s: %s" % (path.split("?")[0], e))

    def discover(self):
        """Pick the ACTIVE connected account of the toolkit (no ids to copy by hand)."""
        if self.account_id:
            return self.account_id
        r = self._call("GET", "/connected_accounts?toolkit_slugs=%s&statuses=ACTIVE&limit=10" % self.toolkit)
        items = r.get("items") or r.get("data") or []
        if not items:
            raise ComposioError("no ACTIVE %s connected account found for this API key" % self.toolkit)
        self.account_id = items[0].get("id")
        self.user_id = self.user_id or items[0].get("user_id")
        return self.account_id

    def execute(self, slug, arguments):
        self.discover()
        body = {"arguments": arguments, "connected_account_id": self.account_id}
        if self.user_id:
            body["user_id"] = self.user_id
        r = self._call("POST", "/tools/execute/%s" % slug, body)
        if r.get("successful") is False or r.get("error"):
            raise ComposioError("%s failed: %s" % (slug, str(r.get("error") or r)[:400]))
        return r.get("data", r)
