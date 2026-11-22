"""The Apps Script control panel (docs/apps_script/lauren/) must stay in step with the engine.

- Its ONE cell map is checked against campaign_lib/sheet.py (CELLS, tabs, queue columns, last row).
- Its constants (sheet id, timezone, Reach url, submit window) are checked against campaign.json.
- Static safety checks on the files, and the node tests (tests/apps_script_panel_test.js) are run.
"""
import json
import os
import re
import shutil
import subprocess
import unittest

from campaign_lib import config, sheet

DIR = os.path.join(config.ROOT, "docs", "apps_script", "lauren")


def read(name):
    with open(os.path.join(DIR, name), encoding="utf-8") as f:
        return f.read()


GS = read("Code.gs")
HTML = read("Index.html")
QUEUE_LABELS = {"n": "#", "source": "Source", "kind": "Kind", "state": "State", "moment": "Moment", "hook": "Hook",
                "caption": "Caption", "note": "Note", "posted_at": "Posted at (UTC)", "media_id": "Media id",
                "link": "Reel link", "submit_before": "Submit before", "updated": "Updated"}


def cell_map_block():
    m = re.search(r"// CELL_MAP_BEGIN\n(.*?)// CELL_MAP_END", GS, re.S)
    assert m, "CELL_MAP_BEGIN/END markers missing in Code.gs"
    return m.group(1)


def js_var(block, name):
    m = re.search(r"var %s = (.*?);\n" % name, block, re.S)
    assert m, "var %s missing in the cell map block" % name
    return m.group(1)


def const(name):
    m = re.search(r"var %s = (?:'([^']*)'|(\d+));" % name, GS)
    assert m, "var %s missing in Code.gs" % name
    return m.group(1) if m.group(1) is not None else int(m.group(2))


class CellMapTests(unittest.TestCase):
    def test_cells_match_sheet_py(self):
        cells = {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", js_var(cell_map_block(), "CELLS"))}
        self.assertEqual(cells, sheet.CELLS)

    def test_tabs_match_sheet_py(self):
        tabs = dict(re.findall(r"(\w+):\s*'([^']*)'", js_var(cell_map_block(), "TABS")))
        self.assertEqual(tabs, {"campaign": sheet.CAMPAIGN, "queue": sheet.QUEUE, "log": sheet.LOG})

    def test_queue_columns_match_sheet_py(self):
        cols = re.findall(r"'([^']*)'", js_var(cell_map_block(), "QUEUE_COLS"))
        self.assertEqual([QUEUE_LABELS[c] for c in cols], sheet.QUEUE_HEAD)

    def test_queue_last_row_matches_sheet_py(self):
        self.assertEqual(int(js_var(cell_map_block(), "QUEUE_LAST_ROW")), sheet.QUEUE_LAST_ROW)

    def test_cell_map_exists_only_once(self):
        self.assertEqual(GS.count("var CELLS ="), 1)
        self.assertNotIn("CELLS", HTML)

    def test_writable_cells_are_exactly_approve_and_add_link_pair(self):
        w = re.findall(r"'(\w+)'", js_var(GS, "WRITABLE"))
        self.assertEqual(sorted(w), ["add_confirm", "add_link", "approve"])
        self.assertTrue(all(k in sheet.CELLS for k in w))
        self.assertEqual(GS.count(".setValue("), 1)  # setCell_ is the only write path


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()

    def test_constants_match_campaign_json(self):
        self.assertEqual(const("SHEET_ID"), self.cfg["sheet"]["id"])
        self.assertEqual(const("TZ"), self.cfg["display_tz"])
        self.assertEqual(const("REACH_URL"), self.cfg["reach_url"])
        self.assertEqual(const("SUBMIT_WINDOW_MIN"), self.cfg["timings"]["submit_window_min"])

    def test_reach_default_in_html_matches(self):
        self.assertIn('href="%s"' % self.cfg["reach_url"], HTML)


class SafetyTests(unittest.TestCase):
    def test_manifest(self):
        m = json.loads(read("appsscript.json"))
        self.assertEqual(m["runtimeVersion"], "V8")
        self.assertEqual(m["webapp"], {"executeAs": "USER_DEPLOYING", "access": "MYSELF"})
        self.assertEqual(m["oauthScopes"], ["https://www.googleapis.com/auth/spreadsheets"])

    def test_code_gs_no_active_spreadsheet_no_network_no_eval(self):
        self.assertIn("SpreadsheetApp.openById(SHEET_ID)", GS)
        for bad in ("getActiveSpreadsheet", "UrlFetchApp", "eval(", "new Function", "ScriptApp", "PropertiesService"):
            self.assertNotIn(bad, GS)

    def test_index_html_never_injects_sheet_text_as_html(self):
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "localStorage"):
            self.assertNotIn(bad, HTML)

    def test_no_secrets_in_files(self):
        for body in (GS, HTML, read("appsscript.json")):
            self.assertIsNone(re.search(r"\b(?:ak|ck|sk|pk)_[A-Za-z0-9]{8,}", body))
            self.assertIsNone(re.search(r"(?i)(api[_-]?key|access[_-]?token)\s*[:=]\s*['\"][^'\"]+", body))

    def test_text_is_at_least_16px(self):
        sizes = [int(x) for x in re.findall(r"font-size:\s*(\d+)px", HTML)]
        self.assertTrue(sizes and min(sizes) >= 16, sizes)
        self.assertRegex(HTML, r"body \{ font: 16px")


class NodeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_node_panel_tests(self):
        p = subprocess.run(["node", os.path.join(config.ROOT, "tests", "apps_script_panel_test.js")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
