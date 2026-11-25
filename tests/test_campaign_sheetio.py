"""SheetIO against a fake Composio client that answers with Google's quota error. No network, no real sleeping."""
import unittest
from datetime import datetime, timezone

from campaign_lib import composio, quota, sheet
from campaign_lib.states import Item

QUOTA_MSG = ("GOOGLESHEETS_BATCH_GET failed: API Error: Quota exceeded for quota metric 'Read requests' and limit "
             "'Read requests per minute' of service 'sheets.googleapis.com' for consumer 'project_number:1'.")
T0 = datetime(2026, 11, 25, 1, 0, 0, tzinfo=timezone.utc)


class FakeSheetClient:
    """Stands in for composio.Client. Holds a tiny sheet; read_quota / write_quota = how many of the next
    requests of that kind are answered with the quota error (the real one is a ComposioError)."""

    def __init__(self, read_quota=0, write_quota=0, other_error=None, down=None):
        self.read_quota, self.write_quota, self.other_error = read_quota, write_quota, other_error
        self.down = down  # optional callable: True while the whole Sheets quota is exhausted (reads AND writes)
        self.cells = {"lock_run": "", "heartbeat": "", "approve": False}
        self.items = [Item(n=1, source="FAKE-1", kind="fake", state="QUEUED")]
        self.calls = []

    def execute(self, slug, arguments):
        self.calls.append(slug)
        if self.other_error:
            raise composio.ComposioError(self.other_error)
        if self.down and self.down():
            raise composio.ComposioError(QUOTA_MSG)
        if slug == "GOOGLESHEETS_BATCH_GET":
            if self.read_quota > 0:
                self.read_quota -= 1
                raise composio.ComposioError(QUOTA_MSG)
            camp = [["", ""] for _ in range(25)]
            for name, row in sheet.CELLS.items():
                camp[row - 1][1] = self.cells.get(name, "")
            rows = [sheet.QUEUE_HEAD] + [sheet.item_row(i, T0) for i in self.items]
            return {"data": {"valueRanges": [{"values": camp}, {"values": rows}]}}
        if slug == "GOOGLESHEETS_UPDATE_VALUES_BATCH":
            if self.write_quota > 0:
                self.write_quota -= 1
                raise composio.ComposioError("HTTP 429 on POST /tools/execute/%s: Too Many Requests" % slug)
            for part in arguments["data"]:
                rng = part["range"]
                if rng.startswith("Queue!A2"):
                    self.items = [sheet.row_item(r) for r in part["values"]]
                if rng.startswith("Campaign!B"):
                    row = int(rng[len("Campaign!B"):])
                    name = [k for k, v in sheet.CELLS.items() if v == row][0]
                    self.cells[name] = part["values"][0][0]
            return {"data": {}}
        raise AssertionError(slug)

    def n(self, slug):
        return self.calls.count(slug)


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def make(client, **kw):
    clk = Clock()
    kw.setdefault("sleep", clk.sleep)
    kw.setdefault("rand", lambda: 0.0)
    return sheet.SheetIO(client, "SHEET", clock=clk, **kw), clk


class QuotaOnRead(unittest.TestCase):
    def test_a_read_waits_and_retries_through_quota_errors(self):
        c = FakeSheetClient(read_quota=3)
        io, clk = make(c)
        snap = io.read()
        self.assertEqual([i.n for i in snap.items], [1])
        self.assertEqual(c.n("GOOGLESHEETS_BATCH_GET"), 4)
        self.assertEqual(clk.sleeps, [2.5, 5.0, 10.0])
        self.assertEqual(io.summary()["quota_waits"], 3)
        self.assertEqual(io.summary()["quota_wait_s"], 18)  # 17.5 s rounded

    def test_waits_grow_and_are_jittered_per_request(self):
        c = FakeSheetClient(read_quota=6)
        io, clk = make(c, rand=lambda: 1.0)
        io.read()
        self.assertEqual(clk.sleeps, [5.0, 10.0, 20.0, 40.0, 60.0, 60.0])

    def test_on_wait_is_told_about_every_wait(self):
        seen = []
        c = FakeSheetClient(read_quota=2)
        io, _ = make(c, on_wait=lambda kind, n, d: seen.append((kind, n, d)))
        io.read()
        self.assertEqual(seen, [("read", 1, 2.5), ("read", 2, 5.0)])

    def test_over_the_wait_budget_is_quotawait_not_a_failure(self):
        c = FakeSheetClient(read_quota=10 ** 6)
        io, clk = make(c, max_wait_s=60, rand=lambda: 1.0)
        with self.assertRaises(quota.QuotaWait):
            io.read()
        self.assertLessEqual(sum(clk.sleeps), 60)
        c.read_quota = 0  # the quota is back: the very next read works, nothing was poisoned
        self.assertEqual([i.n for i in io.read().items], [1])

    def test_a_real_error_is_not_retried(self):
        c = FakeSheetClient(other_error="HTTP 401 on POST /tools/execute/X: Invalid API key")
        io, clk = make(c)
        with self.assertRaises(composio.ComposioError):
            io.read()
        self.assertEqual((c.n("GOOGLESHEETS_BATCH_GET"), clk.sleeps), (1, []))


class QuotaOnWrite(unittest.TestCase):
    def test_a_write_retries_and_lands_exactly_once(self):
        c = FakeSheetClient(write_quota=2)
        io, clk = make(c)
        io.write(T0, values={"heartbeat": "H1"})
        self.assertEqual(c.n("GOOGLESHEETS_UPDATE_VALUES_BATCH"), 3)  # two rejected + one that landed
        self.assertEqual(c.cells["heartbeat"], "H1")
        self.assertEqual(clk.sleeps, [2.5, 5.0])

    def test_write_over_budget_is_quotawait(self):
        c = FakeSheetClient(write_quota=10 ** 6)
        io, _ = make(c, max_wait_s=30, rand=lambda: 1.0)
        with self.assertRaises(quota.QuotaWait):
            io.write(T0, values={"heartbeat": "H1"})
        self.assertEqual(c.cells["heartbeat"], "")  # nothing half-written


class OneBatchedReadAndTheCache(unittest.TestCase):
    def test_one_request_reads_both_tabs(self):
        c = FakeSheetClient()
        seen = []
        orig = c.execute
        c.execute = lambda slug, a: (seen.append(a), orig(slug, a))[1]
        io, _ = make(c)
        io.read()
        self.assertEqual(c.n("GOOGLESHEETS_BATCH_GET"), 1)
        self.assertEqual(len(seen[0]["ranges"]), 2)
        self.assertTrue(seen[0]["ranges"][0].startswith("Campaign!") and seen[0]["ranges"][1].startswith("Queue!"))

    def test_reads_inside_the_ttl_come_from_memory(self):
        c = FakeSheetClient()
        io, clk = make(c)
        io.read()
        clk.t += 20
        io.read()
        io.read()
        self.assertEqual(c.n("GOOGLESHEETS_BATCH_GET"), 1)
        self.assertEqual(io.summary()["cache_hits"], 2)
        clk.t += sheet.CACHE_TTL_S  # older than a tick: the panel may have been used meanwhile
        io.read()
        self.assertEqual(c.n("GOOGLESHEETS_BATCH_GET"), 2)

    def test_a_write_forces_a_re_read(self):
        c = FakeSheetClient()
        io, _ = make(c)
        io.read()
        io.write(T0, values={"heartbeat": "H1"})
        self.assertEqual(io.read().cells["heartbeat"], "H1")
        self.assertEqual(c.n("GOOGLESHEETS_BATCH_GET"), 2)
        io.read()
        self.assertEqual(c.n("GOOGLESHEETS_BATCH_GET"), 2)  # and then it is cached again

    def test_a_failed_write_also_drops_the_cache(self):
        c = FakeSheetClient(write_quota=10 ** 6)
        io, _ = make(c, max_wait_s=1, rand=lambda: 1.0)
        io.read()
        with self.assertRaises(quota.QuotaWait):
            io.write(T0, values={"heartbeat": "H1"})
        io.read()
        self.assertEqual(c.n("GOOGLESHEETS_BATCH_GET"), 2)

    def test_read_fresh_always_goes_to_the_sheet_and_helper_falls_back_for_simple_doubles(self):
        c = FakeSheetClient()
        io, _ = make(c)
        io.read()
        io.read_fresh()
        sheet.read_fresh(io)
        self.assertEqual(c.n("GOOGLESHEETS_BATCH_GET"), 3)

        class Simple:
            def read(self):
                return "plain read"
        self.assertEqual(sheet.read_fresh(Simple()), "plain read")

    def test_callers_get_copies_so_mutating_a_snapshot_cannot_corrupt_the_cache(self):
        c = FakeSheetClient()
        io, _ = make(c)
        a = io.read()
        a.items[0].state = "POSTED"
        a.cells["approve"] = "mutated"
        b = io.read()  # from memory
        self.assertEqual(b.items[0].state, "QUEUED")
        self.assertNotEqual(b.cells["approve"], "mutated")


class Measuring(unittest.TestCase):
    def test_peak_reads_per_minute_counts_requests_sent_including_rejected_ones(self):
        c = FakeSheetClient(read_quota=2)
        io, clk = make(c)
        io.read()  # 3 requests sent (2 rejected) within 7.5 s
        self.assertEqual(io.peak_reads_per_min(), 3)
        clk.t += 600
        for _ in range(3):
            io.read_fresh()
            clk.t += 60  # one read a minute
        self.assertEqual(io.peak_reads_per_min(), 3)  # the burst is still the peak
        self.assertEqual(io.summary()["reads"], 6)


if __name__ == "__main__":
    unittest.main()
