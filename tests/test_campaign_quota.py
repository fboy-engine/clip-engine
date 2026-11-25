"""Quota handling: a fake sheet client that answers with Google's quota error. No network, no real sleeping."""
import io
import json
import unittest
import urllib.error
from unittest import mock

from campaign_lib import composio, quota

QUOTA_MSG = ("GOOGLESHEETS_BATCH_GET failed: API Error: Quota exceeded for quota metric 'Read requests' and limit "
             "'Read requests per minute' of service 'sheets.googleapis.com' for consumer 'project_number:1'.")


class Detection(unittest.TestCase):
    def test_the_real_google_message_and_other_quota_shapes_are_recognised(self):
        for msg in (QUOTA_MSG, "HTTP 429 on POST /tools/execute/GOOGLESHEETS_BATCH_GET: slow down",
                    "RESOURCE_EXHAUSTED", "userRateLimitExceeded", "rateLimitExceeded", "Too Many Requests",
                    "Quota exceeded for quota metric 'Write requests' and limit 'Write requests per minute'"):
            self.assertTrue(quota.is_quota_error(composio.ComposioError(msg)), msg)

    def test_other_errors_are_not_quota_errors(self):
        for msg in ("HTTP 401 on GET /x: Invalid API key", "unexpected BATCH_GET answer (no valueRanges)",
                    "network error on /tools/execute: timed out", "HTTP 500 on POST /x", "sheet id is empty"):
            self.assertFalse(quota.is_quota_error(composio.ComposioError(msg)), msg)


class Delay(unittest.TestCase):
    def test_exponential_with_equal_jitter_and_a_cap(self):
        lo = lambda n: quota.backoff_delay(n, rand=lambda: 0.0)
        hi = lambda n: quota.backoff_delay(n, rand=lambda: 1.0)
        self.assertEqual([lo(n) for n in range(4)], [2.5, 5.0, 10.0, 20.0])
        self.assertEqual([hi(n) for n in range(4)], [5.0, 10.0, 20.0, 40.0])
        for n in (4, 5, 10, 50):
            self.assertEqual((lo(n), hi(n)), (30.0, 60.0))  # capped at one minute

    def test_the_jitter_really_spreads_the_waits(self):
        vals = {round(quota.backoff_delay(3, rand=lambda r=r: r), 3) for r in (0.0, 0.25, 0.5, 0.75, 1.0)}
        self.assertEqual(len(vals), 5)
        self.assertTrue(all(20.0 <= v <= 40.0 for v in vals))


class Call(unittest.TestCase):
    def fn_failing(self, times, msg=QUOTA_MSG):
        state = {"n": 0}

        def fn():
            state["n"] += 1
            if state["n"] <= times:
                raise composio.ComposioError(msg)
            return "ok"
        return fn, state

    def test_retries_quota_errors_then_returns(self):
        fn, st = self.fn_failing(3)
        sleeps, waits = [], []
        out = quota.call(fn, sleep=sleeps.append, rand=lambda: 0.0, on_wait=lambda a, d: waits.append((a, d)))
        self.assertEqual(out, "ok")
        self.assertEqual(st["n"], 4)
        self.assertEqual(sleeps, [2.5, 5.0, 10.0])
        self.assertEqual(waits, [(0, 2.5), (1, 5.0), (2, 10.0)])

    def test_a_non_quota_error_is_raised_at_once_without_waiting(self):
        fn, st = self.fn_failing(5, "HTTP 401 on GET /x: Invalid API key")
        sleeps = []
        with self.assertRaises(composio.ComposioError):
            quota.call(fn, sleep=sleeps.append)
        self.assertEqual((st["n"], sleeps), (1, []))

    def test_unlimited_wait_keeps_going_through_a_long_outage(self):
        fn, st = self.fn_failing(40)
        sleeps = []
        self.assertEqual(quota.call(fn, sleep=sleeps.append, rand=lambda: 1.0), "ok")
        self.assertEqual(len(sleeps), 40)
        self.assertEqual(max(sleeps), 60.0)

    def test_a_wait_budget_ends_in_quotawait_not_in_a_failure(self):
        fn, st = self.fn_failing(10 ** 6)
        sleeps = []
        with self.assertRaises(quota.QuotaWait) as cm:
            quota.call(fn, sleep=sleeps.append, rand=lambda: 1.0, max_wait_s=100)
        self.assertLessEqual(sum(sleeps), 100)
        self.assertNotIsInstance(cm.exception, composio.ComposioError)
        self.assertIsInstance(cm.exception.__cause__, composio.ComposioError)
        self.assertFalse(quota.is_quota_error(cm.exception))  # an outer retry must not retry a QuotaWait again

    def test_quotawait_from_an_inner_call_is_not_retried_by_an_outer_one(self):
        outer_sleeps = []

        def inner():
            raise quota.QuotaWait("inner gave up")
        with self.assertRaises(quota.QuotaWait):
            quota.call(inner, sleep=outer_sleeps.append)
        self.assertEqual(outer_sleeps, [])


class _Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


class HttpLayer(unittest.TestCase):
    def client(self):
        return composio.Client(api_key="ck_FAKE123", account_id="acc")

    def test_http_429_is_not_blindly_retried_and_reads_as_a_quota_error(self):
        calls = []

        def fake(req, timeout=0):
            calls.append(1)
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, io.BytesIO(b'{"error":"slow down"}'))

        with mock.patch.object(composio.urllib.request, "urlopen", fake), mock.patch.object(composio.time, "sleep") as sl:
            with self.assertRaises(composio.ComposioError) as cm:
                self.client().execute("GOOGLESHEETS_BATCH_GET", {})
        self.assertEqual(len(calls), 1)
        sl.assert_not_called()
        self.assertTrue(quota.is_quota_error(cm.exception))

    def test_a_500_still_gets_its_one_quick_retry(self):
        calls = []

        def fake(req, timeout=0):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b"{}"))
            return _Resp(json.dumps({"successful": True, "data": {"ok": 1}}).encode())

        with mock.patch.object(composio.urllib.request, "urlopen", fake), mock.patch.object(composio.time, "sleep"):
            self.assertEqual(self.client().execute("X", {}), {"ok": 1})
        self.assertEqual(len(calls), 2)

    def test_googles_quota_text_inside_a_200_answer_becomes_a_quota_error(self):
        body = {"successful": False, "error": QUOTA_MSG[len("GOOGLESHEETS_BATCH_GET failed: "):]}

        def fake(req, timeout=0):
            return _Resp(json.dumps(body).encode())

        with mock.patch.object(composio.urllib.request, "urlopen", fake):
            with self.assertRaises(composio.ComposioError) as cm:
                self.client().execute("GOOGLESHEETS_BATCH_GET", {})
        self.assertTrue(quota.is_quota_error(cm.exception))


if __name__ == "__main__":
    unittest.main()
