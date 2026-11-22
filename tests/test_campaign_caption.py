import copy
import unittest

from campaign_lib import caption, config


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load("lauren-hamden")

    def test_reach_rule_both_exact_ascii_strings(self):
        # Reach rule, literal on purpose. If the handle in campaign.json changes (one-line change),
        # change these two strings with it.
        c = caption.build_caption(self.cfg, "Lauren could not stop laughing at this one")
        self.assertIn("#LaurenHamden", c)
        self.assertIn("@LaurenHamden", c)
        self.assertTrue(c.isascii())

    def test_tags_follow_config_handle(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["handle"] = "SomeoneElse"
        c = caption.build_caption(cfg, "Fun moment")
        self.assertIn("#SomeoneElse", c)
        self.assertIn("@SomeoneElse", c)
        self.assertNotIn("LaurenHamden", c)

    def test_one_line_and_at_most_five_extra_hashtags(self):
        c = caption.build_caption(self.cfg, "Wild chase", ["#GTA6", "#Twitch", "#Gaming", "#GTAVI", "#Funny"])
        body, tags = c.split("\n\n")
        self.assertNotIn("\n", body)
        self.assertEqual(len([t for t in tags.split() if t.startswith("#")]), 1 + 5)
        with self.assertRaises(caption.CaptionError):
            caption.build_caption(self.cfg, "Wild chase", ["#a1", "#b2", "#c3", "#d4", "#e5", "#f6"])

    def test_default_tags_used(self):
        c = caption.build_caption(self.cfg, "Nice")
        for t in self.cfg["caption"]["default_extra_hashtags"]:
            self.assertIn(t, c)

    def test_required_tag_not_duplicated(self):
        c = caption.build_caption(self.cfg, "Nice", ["#laurenhamden", "#GTA6"])
        self.assertEqual(c.lower().count("#laurenhamden"), 1)

    def test_spam_and_bad_tags_rejected(self):
        for bad in (["#fyp"], ["#Viral"], ["no_hash"], ["#bad tag"], ["#"]):
            with self.assertRaises(caption.CaptionError):
                caption.build_caption(self.cfg, "Nice", bad)

    def test_line_rules(self):
        for bad in ("", "   ", "x" * 200, "café time", "look \U0001F602", "see #GTA6", "ping @someone",
                    "watch http://x.y", "line one\nline two"):
            if bad == "line one\nline two":
                # whitespace is normalised to ONE line, never two
                self.assertNotIn("\n", caption.build_caption(self.cfg, bad).split("\n\n")[0])
                continue
            with self.assertRaises(caption.CaptionError, msg=repr(bad)):
                caption.build_caption(self.cfg, bad)

    def test_verify_caption_fails_without_either_string(self):
        with self.assertRaises(caption.CaptionError):
            caption.verify_caption(self.cfg, "Nice\n\n#LaurenHamden #GTA6")
        with self.assertRaises(caption.CaptionError):
            caption.verify_caption(self.cfg, "Nice\n\n@LaurenHamden #GTA6")

    def test_handle_validation(self):
        cfg = copy.deepcopy(self.cfg)
        for bad in ("#Lauren", "@Lauren", "Lauren Hamden", "Läuren", ""):
            cfg["handle"] = bad
            with self.assertRaises(ValueError):
                config.validate(cfg)


if __name__ == "__main__":
    unittest.main()
