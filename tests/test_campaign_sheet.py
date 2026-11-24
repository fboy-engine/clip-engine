import unittest
from datetime import datetime, timezone

from campaign_lib import config, sheet

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


class ReachCellTests(unittest.TestCase):
    def test_reach_url_is_one_config_value(self):
        cfg = config.load()
        self.assertEqual(cfg["reach_url"], "https://reachclipping.com/Sisters")
        p = sheet.reach_payload(cfg)
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]["range"], "Campaign!C10")
        self.assertEqual(p[0]["values"][0][0],
                         '=HYPERLINK("https://reachclipping.com/Sisters","Open Reach submit page")')

    def test_no_reach_url_no_cell(self):
        self.assertEqual(sheet.reach_payload({}), [])

    def test_bad_reach_url_rejected(self):
        cfg = config.load()
        for bad in ("http://x.com", 'https://x.com/a"b', "https://x.com/a b"):
            cfg["reach_url"] = bad
            with self.assertRaises(ValueError):
                config.validate(cfg)

    def test_init_payload_stays_raw_and_leaves_reach_cell_alone(self):
        cfg = config.load()
        data = sheet.init_payload(cfg, NOW)
        camp = data[0]["values"]
        self.assertEqual(len(camp[0]), 2)  # Campaign block is A:B only; C10 is written by reach_payload
        self.assertNotIn("HYPERLINK", str(data))


class RunColumnTests(unittest.TestCase):
    def test_run_id_round_trips_and_old_13_column_rows_still_read(self):
        from campaign_lib.states import Item
        it = Item(n=3, source="https://m.twitch.tv/x/clip/Abc", run="45300515662")
        row = sheet.item_row(it, NOW)
        self.assertEqual(len(row), 14)
        self.assertEqual(sheet.row_item(row).run, "45300515662")
        self.assertEqual(sheet.row_item(row[:13]).run, "")

    def test_real_upload_switch_is_a_machine_cell_and_off_by_default(self):
        self.assertEqual(sheet.CELLS["real_upload"], 23)
        data = sheet.init_payload(config.load(), NOW)
        camp = data[0]["values"]
        self.assertEqual(len(camp), 23)
        self.assertEqual(camp[22][1], "")


if __name__ == "__main__":
    unittest.main()
