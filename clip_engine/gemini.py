"""Gemini client: runtime model discovery, fail-fast retries, model fallback.

The old 404 came from a hardcoded model name Google retired, so we never trust
a constant: GEMINI_MODEL (if set) goes FIRST, then ListModels' newest flash
models follow behind it as fallbacks. 503 "high demand" and stalls are per-model, so each model gets few
tries and then the next candidate is used; -latest aliases are last resorts.
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
MAX_MODELS = 5
ALIASES = ["gemini-flash-latest", "gemini-flash-lite-latest"]
DEADLINE = 75  # seconds for the whole generate() call
MAX_CALLS = 4  # total generateContent calls per generate(), across the whole chain
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


def candidates(model_names, max_models=MAX_MODELS):
    """Ranked flash/flash-lite names, then the -latest aliases as last resorts.
    A 503 'high demand' is per-model, so a longer, mixed chain gets through."""
    out = rank_models(model_names)[:max_models]
    for a in ALIASES:
        if a not in out:
            out.append(a)
    return out


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


def generate(prompt, key=None, retries=2, timeout=25, opener=_http, sleep=time.sleep,
             max_chars=6000, max_models=MAX_MODELS, deadline=DEADLINE, clock=time.monotonic,
             max_calls=MAX_CALLS, images=None, audio=None):
    """Short prompt in, text out. `images` = base64 JPEG strings sent with the prompt
    (the 'watching' gate); `audio` = (mime, base64) sent with the prompt (caption check). Fails fast: each model gets `retries` tries
    (429/5xx/timeouts, short backoff), then the next candidate. A whole-call
    deadline and a total call cap (`max_calls`) stop long chains.
    Auth/bad-request errors stop at once."""
    key = key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise GeminiUnavailable("GEMINI_API_KEY not set")
    prompt = prompt[:max_chars]  # shorter inputs = fewer ReadTimeouts
    t0 = clock()
    env = os.environ.get("GEMINI_MODEL")
    try:
        listed = candidates(list_models(key, opener), max_models)
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        if not env:
            raise GeminiUnavailable("ListModels failed")
        listed = []  # pinned model still works without ListModels
    # GEMINI_MODEL LEADS the chain; the ranked candidates stay behind it
    models = ([env] if env else []) + [m for m in listed if m != env]
    if not models:
        raise GeminiUnavailable("no usable flash model in ListModels")
    parts = [{"text": prompt}] + [{"inline_data": {"mime_type": "image/jpeg", "data": b}}
                                  for b in (images or [])]
    if audio:
        parts.append({"inline_data": {"mime_type": audio[0], "data": audio[1]}})
    body = {"contents": [{"parts": parts}]}
    notes = []
    calls = 0
    for model in models:
        last = None
        for i in range(retries):
            left = deadline - (clock() - t0)
            if left <= 1:
                notes.append(f"{model}: deadline")
                raise GeminiUnavailable("; ".join(notes))
            if calls >= max_calls:
                notes.append(f"{model}: call cap {max_calls}")
                raise GeminiUnavailable("; ".join(notes))
            calls += 1
            try:
                data = opener(f"{BASE}/models/{model}:generateContent?key={key}", body,
                              min(timeout, left))
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
                sleep(min(2 ** i, 4))
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
