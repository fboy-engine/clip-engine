"""Logging for a PUBLIC repo: stdout of the workflow is world readable.

Everything printed goes through redact(): every URL (Reach, stream, Reel links), every known secret value,
token-shaped strings and any long opaque string (sheet ids, keys) are replaced. Callers also log only
counts, state names and ids of runs, never captions or sources."""
import os
import re
import sys
from datetime import datetime, timezone

_PATTERNS = [
    (re.compile(r"https?://\S+", re.I), "<url>"),
    (re.compile(r"\bBearer\s+\S+", re.I), "Bearer ***"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_\w+)"), "***"),
    (re.compile(r"\b(?:ck|ak|sk|pk)_[A-Za-z0-9*_\-]*"), "***"),
    (re.compile(r"\bEA[A-Za-z0-9]{30,}"), "***"),
    (re.compile(r"[A-Za-z0-9_\-]{32,}"), "<redacted>"),
]


def secret_values(env_names, environ=None):
    environ = os.environ if environ is None else environ
    vals = []
    for n in env_names:
        v = (environ.get(n) or "").strip().strip("\"'").strip()
        if len(v) >= 6:
            vals.append(v)
    return sorted(set(vals), key=len, reverse=True)


def redact(text, secrets=()):
    text = str(text)
    for v in secrets:
        text = text.replace(v, "***")
    for rx, rep in _PATTERNS:
        text = rx.sub(rep, text)
    return text


class Logger:
    def __init__(self, secrets=(), stream=None, clock=None):
        self.secrets = list(secrets)
        self.stream = stream or sys.stdout
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def __call__(self, event, **fields):
        parts = ["%s=%s" % (k, redact(v, self.secrets)) for k, v in fields.items()]
        line = "%s %s %s" % (self.clock().strftime("%H:%M:%S"), event, " ".join(parts))
        print(line.rstrip(), file=self.stream, flush=True)

    def err(self, e):
        """Exception text, redacted, one line, short."""
        return redact(("%s: %s" % (type(e).__name__, e)).replace("\n", " "), self.secrets)[:200]
