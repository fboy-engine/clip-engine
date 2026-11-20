"""Gemini client: runtime model discovery, retries with backoff, local fallback.

The old 404 came from a hardcoded model name that Google retired. Model names
change, so we never trust a constant: GEMINI_MODEL env wins, else ask ListModels
and pick the newest flash model, else raise so callers use the local fallback.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta"
PREFERENCE = [r"gemini-(\d+(?:\.\d+)?)-flash$", r"gemini-(\d+(?:\.\d+)?)-flash-lite$"]


class GeminiUnavailable(Exception):
    """Raised when Gemini cannot answer; callers must use the local fallback."""


def pick_model(model_names):
    """Pick newest stable flash model from ListModels names ('models/x')."""
    names = [n.split("/", 1)[-1] for n in model_names]
    for pat in PREFERENCE:
        best = None
        for n in names:
            m = re.fullmatch(pat, n)
            if m:
                ver = tuple(int(p) for p in m.group(1).split("."))
                if best is None or ver > best[0]:
                    best = (ver, n)
        if best:
            return best[1]
    return None


def _http(url, body=None, timeout=60):
    req = urllib.request.Request(
        url, data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def resolve_model(key, opener=_http):
    env = os.environ.get("GEMINI_MODEL")
    if env:
        return env
    data = opener(f"{BASE}/models?key={key}&pageSize=200", None, 30)
    ok = [m["name"] for m in data.get("models", [])
          if "generateContent" in m.get("supportedGenerationMethods", [])]
    model = pick_model(ok)
    if not model:
        raise GeminiUnavailable("no usable flash model in ListModels")
    return model


def generate(prompt, key=None, retries=4, timeout=45, opener=_http, sleep=time.sleep,
             max_chars=6000):
    """Short prompt in, text out. Retries 429/5xx/timeouts with backoff.

    The URL contains the key, so it is never printed or put in an exception.
    """
    key = key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise GeminiUnavailable("GEMINI_API_KEY not set")
    prompt = prompt[:max_chars]  # shorter inputs = fewer ReadTimeouts
    try:
        model = resolve_model(key, opener)
    except (urllib.error.URLError, OSError, ValueError):
        raise GeminiUnavailable("ListModels failed")
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    last = None
    for i in range(retries):
        try:
            data = opener(f"{BASE}/models/{model}:generateContent?key={key}", body, timeout)
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code == 404:
                raise GeminiUnavailable(f"model {model} 404; set GEMINI_MODEL")
            if e.code not in (429, 500, 502, 503, 504):
                raise GeminiUnavailable(last)
        except (TimeoutError, urllib.error.URLError, OSError) as e:
            last = type(e).__name__
        except (KeyError, IndexError, ValueError):
            raise GeminiUnavailable("malformed response")
        sleep(min(2 ** i, 20))
    raise GeminiUnavailable(f"gave up after {retries} tries ({last})")
