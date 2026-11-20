"""Gemini client: runtime model discovery, retries with backoff, model fallback.

The old 404 came from a hardcoded model name Google retired, so we never trust
a constant: GEMINI_MODEL env wins, else ListModels and take the newest flash
models. A model that stalls (ReadTimeout) is skipped for the next candidate.
Callers must treat GeminiUnavailable as "use the local fallback".
The URL contains the key, so it is never printed or put in an exception.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta"
PREFERENCE = [r"gemini-(\d+(?:\.\d+)?)-flash$", r"gemini-(\d+(?:\.\d+)?)-flash-lite$"]
MAX_MODELS = 3
RETRY_CODES = (429, 500, 502, 503, 504)


class GeminiUnavailable(Exception):
    """Raised when Gemini cannot answer; callers must use the local fallback."""


def rank_models(model_names):
    """Newest flash first, then newest flash-lite. Names like 'models/x' ok."""
    names = [n.split("/", 1)[-1] for n in model_names]
    ranked = []
    for pat in PREFERENCE:
        found = []
        for n in names:
            m = re.fullmatch(pat, n)
            if m:
                found.append((tuple(int(p) for p in m.group(1).split(".")), n))
        found.sort(reverse=True)
        for _, n in found:
            if n not in ranked:
                ranked.append(n)
    return ranked


def pick_model(model_names):
    """Newest stable flash model from ListModels names, or None."""
    ranked = rank_models(model_names)
    return ranked[0] if ranked else None


def _http(url, body=None, timeout=60):
    req = urllib.request.Request(
        url, data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _err_detail(e):
    try:
        msg = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
    except Exception:
        msg = ""
    return f"HTTP {e.code} {msg[:120]}".strip()


def list_models(key, opener=_http):
    data = opener(f"{BASE}/models?key={key}&pageSize=200", None, 30)
    return [m["name"] for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]


def resolve_model(key, opener=_http):
    env = os.environ.get("GEMINI_MODEL")
    if env:
        return env
    model = pick_model(list_models(key, opener))
    if not model:
        raise GeminiUnavailable("no usable flash model in ListModels")
    return model


def generate(prompt, key=None, retries=4, timeout=45, opener=_http, sleep=time.sleep,
             max_chars=6000, max_models=MAX_MODELS):
    """Short prompt in, text out. Retries 429/5xx/timeouts with backoff, then
    moves to the next candidate model. Auth/bad-request errors stop at once."""
    key = key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise GeminiUnavailable("GEMINI_API_KEY not set")
    prompt = prompt[:max_chars]  # shorter inputs = fewer ReadTimeouts
    env = os.environ.get("GEMINI_MODEL")
    try:
        models = [env] if env else rank_models(list_models(key, opener))[:max_models]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        raise GeminiUnavailable("ListModels failed")
    if not models:
        raise GeminiUnavailable("no usable flash model in ListModels")
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    notes = []
    for model in models:
        last = None
        for i in range(retries):
            try:
                data = opener(f"{BASE}/models/{model}:generateContent?key={key}", body, timeout)
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as e:
                last = _err_detail(e)
                if e.code == 404:
                    break  # retired/unknown model: try the next one
                if e.code not in RETRY_CODES:
                    raise GeminiUnavailable(f"{model}: {last}")
            except (TimeoutError, urllib.error.URLError, OSError) as e:
                last = type(e).__name__
            except (KeyError, IndexError, ValueError):
                last = "malformed response"
                break
            if i < retries - 1:
                sleep(min(2 ** i, 20))
        notes.append(f"{model}: {last}")
    raise GeminiUnavailable("; ".join(notes))


def probe(key, model, timeout=25, opener=_http, clock=time.monotonic):
    """One call to one model. Returns (ok, detail, seconds). For diagnostics."""
    t = clock()
    body = {"contents": [{"parts": [{"text": "Reply with exactly the single word: OK"}]}]}
    try:
        data = opener(f"{BASE}/models/{model}:generateContent?key={key}", body, timeout)
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return True, f"reply={text.strip()[:20]!r}", clock() - t
    except urllib.error.HTTPError as e:
        return False, _err_detail(e), clock() - t
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        return False, type(e).__name__, clock() - t
    except (KeyError, IndexError, ValueError):
        return False, "malformed response", clock() - t
