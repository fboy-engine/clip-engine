import io
import os
import unittest
import urllib.error
from unittest import mock

from campaign_lib import composio


class ScrubTests(unittest.TestCase):
    def test_full_key_and_masked_echo_removed(self):
        key = "ak_FAKEKEYFORTEST123"
        msg = 'bad %s and Invalid API key: ck_**tE6X' % key
        out = composio.scrub(msg, key)
        self.assertNotIn(key, out)
        self.assertNotIn("tE6X", out)
        self.assertNotIn("ck_", out)

    def test_plain_text_untouched(self):
        self.assertEqual(composio.scrub("HTTP 404 not found"), "HTTP 404 not found")


class _Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def _http_401():
    return urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b'{"error":{"message":"Invalid API key: ***"}}'))


class KeyCleanupTests(unittest.TestCase):
    def test_whitespace_and_quotes_stripped(self):
        self.assertEqual(composio.Client(api_key=' "ak_FAKE"\n').api_key, "ak_FAKE")
        self.assertEqual(composio.Client(api_key="ak_FAKE").api_key, "ak_FAKE")

    def test_blank_key_is_not_set(self):
        os.environ.pop("COMPOSIO_API_KEY", None)
        with self.assertRaises(composio.ComposioError):
            composio.Client(api_key=" \n ")


class AuthHeaderFallbackTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("COMPOSIO_AUTH_HEADER", None)

    def test_401_falls_back_to_consumer_header_once_and_sticks(self):
        seen = []

        def fake(req, timeout=0):
            seen.append("x-consumer-api-key" if req.get_header("X-consumer-api-key") else "x-api-key")
            if seen[-1] == "x-api-key":
                raise _http_401()
            return _Resp(b'{"items": []}')

        c = composio.Client(api_key="ck_FAKE123")
        with mock.patch.object(composio.urllib.request, "urlopen", fake):
            c._call("GET", "/connected_accounts")
            c._call("GET", "/connected_accounts")
        self.assertEqual(seen, ["x-api-key", "x-consumer-api-key", "x-consumer-api-key"])

    def test_both_headers_401_raises_scrubbed_error(self):
        def fake(req, timeout=0):
            raise _http_401()

        c = composio.Client(api_key="ck_FAKE123")
        with mock.patch.object(composio.urllib.request, "urlopen", fake):
            with self.assertRaises(composio.ComposioError) as cm:
                c._call("GET", "/connected_accounts")
        self.assertIn("401", str(cm.exception))
        self.assertIn("Invalid API key", str(cm.exception))
        self.assertIn("also rejected", str(cm.exception))
        self.assertNotIn("ck_FAKE123", str(cm.exception))

    def test_pinned_header_env_disables_fallback(self):
        calls = []

        def fake(req, timeout=0):
            calls.append(1)
            raise _http_401()

        os.environ["COMPOSIO_AUTH_HEADER"] = "x-api-key"
        try:
            c = composio.Client(api_key="ak_FAKE")
            with mock.patch.object(composio.urllib.request, "urlopen", fake):
                with self.assertRaises(composio.ComposioError):
                    c._call("GET", "/x")
        finally:
            os.environ.pop("COMPOSIO_AUTH_HEADER", None)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
