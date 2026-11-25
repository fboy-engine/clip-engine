"""Quota / rate-limit handling for the Google Sheets calls that go through Composio.

Why this exists: Google's "Read requests per minute" limit is counted on the Google project that Composio's
shared OAuth app uses, so it can be used up by other callers too. A quota answer therefore says nothing about
our own read rate and must never fail a run: wait (exponential back-off with jitter) and try again.

  is_quota_error(e)  : True for a 429 / "Quota exceeded" / RESOURCE_EXHAUSTED / rateLimitExceeded answer
  backoff_delay(n)   : seconds to wait before retry n (0-based): equal jitter, doubles each time, capped
  call(fn, ...)      : run fn, retry on quota errors only; after max_wait_s of waiting raise QuotaWait
  QuotaWait          : "still over quota after the allowed wait". NOT a failure: loop, watchdog and CLI treat it
                       as "try again later" and never exit non-zero because of it.
"""
import random
import re
import time

BASE_S = 5.0      # ceiling of the first wait; the real wait is between half of it and all of it
CAP_S = 60.0      # Google's per-minute window: a wait never needs to be longer than a minute

_QUOTA = re.compile(
    r"quota exceeded|quota metric|rate[ _-]?limit|resource[_ ]exhausted|too many requests"
    r"|(?:read|write) requests per minute|\bHTTP 429\b", re.I)


class QuotaWait(RuntimeError):
    """Still over quota after the allowed waiting time. The caller carries on / retries later."""


def is_quota_error(err):
    return bool(_QUOTA.search(str(err)))


def backoff_delay(attempt, base=BASE_S, cap=CAP_S, rand=random.random):
    """Equal-jitter exponential back-off: ceiling = min(cap, base * 2**attempt); wait = ceiling/2 .. ceiling.
    The jitter keeps a watchdog, a loop and a starting successor from retrying in lockstep."""
    ceiling = min(float(cap), float(base) * (2 ** max(0, int(attempt))))
    return ceiling / 2.0 + rand() * ceiling / 2.0


def call(fn, sleep=time.sleep, rand=random.random, max_wait_s=None, base=BASE_S, cap=CAP_S, on_wait=None):
    """fn() with retries on quota errors ONLY (any other exception passes straight through).
    max_wait_s None = wait for as long as it takes. Otherwise, once the next wait would push the total past
    max_wait_s, QuotaWait is raised (from the last quota error). on_wait(attempt, delay) runs before each sleep."""
    waited, attempt = 0.0, 0
    while True:
        try:
            return fn()
        except QuotaWait:
            raise
        except Exception as e:
            if not is_quota_error(e):
                raise
            delay = backoff_delay(attempt, base, cap, rand)
            if max_wait_s is not None and waited + delay > max_wait_s:
                raise QuotaWait("sheet quota still exceeded after %d tries and %.0f s of waiting" % (attempt + 1, waited)) from e
            if on_wait:
                on_wait(attempt, delay)
            sleep(delay)
            waited += delay
            attempt += 1
