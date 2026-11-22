import unittest

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


if __name__ == "__main__":
    unittest.main()
