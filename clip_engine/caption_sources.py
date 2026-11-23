"""Second sources for the caption check: Gemini's transcription of the clip audio, OCR of the game's own subtitles,
and a rescue ASR pass over speech the first pass missed.

Everything here returns plain data for captions_verify.verify(); nothing is guessed and nothing is posted.
OCR is tesseract (CLI) on a lower-centre band of the SOURCE frame with the cam / chat / counter rects blanked.
The pure parts (merge_lines, uncovered, rescue, parse_tsv) are unit-tested; ffmpeg/tesseract/whisper are injected."""
import base64
import difflib
import io
import re
import shutil
import subprocess
import wave

import numpy as np

from . import captions_verify as cv
from . import gemini, transcribe

SR = transcribe.SR
OCR_FPS = 2.0
OCR_BAND = (0.10, 0.60, 0.90, 0.98)     # x0, y0, x1, y1 as fractions of the source frame
OCR_MIN_CONF = 60.0
OCR_SAME = 0.80                         # two OCR readings this alike are one subtitle line
OCR_MIN_SAMPLES = 2                     # a line must be read on this many samples (or be very sure)
OCR_SURE = 85.0
RESCUE_MIN_LEN = 0.30                   # uncovered speech shorter than this is not worth a pass
RESCUE_CTX = 0.35
RESCUE_MIN_P = 0.45
RESCUE_MAX_SEC = 25.0
HALLUCINATIONS = {"thank you", "thanks for watching", "you", "bye", "thank you for watching"}
GEMINI_PROMPT = ("Transcribe exactly what is said in this audio, word for word, in the order spoken. Plain text only: "
                 "no timestamps, no speaker labels, no commentary, no summary. Keep profanity as spoken. If a word is "
                 "unclear write your best hearing of it. If there is no speech reply with an empty line.")


# ---------- Gemini audio text ----------
def wav_b64(pcm, sr=SR):
    a = np.clip(np.asarray(pcm, dtype=np.float32), -1.0, 1.0)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((a * 32767).astype(np.int16).tobytes())
    return base64.b64encode(buf.getvalue()).decode()


def clean_gemini(text):
    t = re.sub(r"```[a-z]*", "", text or "")
    t = re.sub(r"\[[^\]]{0,40}\]|\([^)]{0,40}\)", " ", t)      # [music], (laughs)
    t = re.sub(r"(?im)^\s*(transcript|transcription)\s*:\s*", "", t)
    return re.sub(r"\s+", " ", t).strip()


_GEMINI_OFF = None      # set once Gemini answers 401/403 (no access / free tier): later calls skip the network


def gemini_text(pcm, key=None, generate=gemini.generate):
    """-> (text or None, error or None). Never raises: Gemini being down only removes a second source.
    An access error (401/403) is final for this process: one call, one short message, no retries."""
    global _GEMINI_OFF
    if _GEMINI_OFF:
        return None, _GEMINI_OFF
    try:
        raw = generate(GEMINI_PROMPT, key=key, timeout=60, deadline=100, max_calls=1, retries=1,
                       audio=("audio/wav", wav_b64(pcm)))
    except gemini.GeminiUnavailable as e:
        msg = str(e)
        m = re.search(r"HTTP (401|403)", msg)
        if m:
            _GEMINI_OFF = f"HTTP {m.group(1)}, no Gemini access for this key (free tier / billing)"
            return None, _GEMINI_OFF
        return None, msg[:200]
    except Exception as e:                       # network oddities must not kill the render
        return None, f"{type(e).__name__}: {str(e)[:120]}"
    return clean_gemini(raw), None


# ---------- OCR of the game's subtitles ----------
def tesseract_available(which=shutil.which):
    return which("tesseract") is not None


def band_px(src_w, src_h, band=OCR_BAND):
    x0, y0, x1, y1 = band
    return (int(src_w * x0) // 2 * 2, int(src_h * y0) // 2 * 2,
            int(src_w * (x1 - x0)) // 2 * 2, int(src_h * (y1 - y0)) // 2 * 2)


def ocr_cmd(media, start, dur, src_w, src_h, fps=OCR_FPS, band=OCR_BAND):
    x, y, w, h = band_px(src_w, src_h, band)
    return ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:g}", "-t", f"{dur:g}", "-i", media,
            "-vf", f"fps={fps:g},crop={w}:{h}:{x}:{y},format=gray", "-f", "rawvideo", "-"], (w, h)


def blank(gray, rects_px, origin):
    """Zero the part of a band frame inside any rect (source pixels): cam, chat, counters are not subtitles."""
    ox, oy = origin
    out = gray.copy()
    for x0, y0, x1, y1 in rects_px or []:
        a, b = max(0, int(x0 - ox)), max(0, int(y0 - oy))
        c, d = min(out.shape[1], int(x1 - ox)), min(out.shape[0], int(y1 - oy))
        if c > a and d > b:
            out[b:d, a:c] = 0
    return out


def prep(gray):
    """Bright subtitle text -> black on white, doubled in size (tesseract likes ~30 px letters)."""
    import cv2
    thr = max(185, int(np.percentile(gray, 99.3)) - 12)
    mask = (gray >= thr).astype(np.uint8) * 255
    mask = cv2.resize(mask, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
    mask = cv2.dilate(mask, np.ones((2, 2), np.uint8))
    return 255 - mask


def parse_tsv(tsv, min_conf=OCR_MIN_CONF):
    """tesseract tsv -> [(line_text, mean_conf)] for lines that look like words."""
    lines = {}
    for row in (tsv or "").splitlines()[1:]:
        p = row.split("\t")
        if len(p) < 12 or p[0] != "5":
            continue
        try:
            conf = float(p[10])
        except ValueError:
            continue
        txt = p[11].strip()
        if txt and conf >= 0:
            lines.setdefault((p[2], p[3], p[4]), []).append((txt, conf))
    out = []
    for ws in lines.values():
        good = [(t, c) for t, c in ws if c >= min_conf and re.search(r"[A-Za-z]{2}", t)]
        if not good:
            continue
        text = " ".join(t for t, _ in ws if re.search(r"\w", t))
        mean = sum(c for _, c in good) / len(good)
        if len(good) / len(ws) >= 0.6 and mean >= min_conf:
            out.append((text, mean))
    return out


def tesseract_frame(gray, run=subprocess.run):
    import cv2
    ok, png = cv2.imencode(".png", prep(gray))
    p = run(["tesseract", "stdin", "stdout", "--psm", "6", "-l", "eng", "tsv"],
            input=png.tobytes(), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("tesseract failed: " + p.stderr.decode(errors="replace")[-200:])
    return parse_tsv(p.stdout.decode(errors="replace"))


def merge_lines(samples, fps=OCR_FPS):
    """samples: [(t, [(text, conf)])] in time order -> [{'s','e','text','conf','samples'}], one per subtitle line."""
    active, done = [], []
    for t, found in samples:
        seen = set()
        for text, conf in found:
            norm = re.sub(r"[^a-z0-9' ]", "", text.lower()).strip()
            if not norm:
                continue
            hit = None
            for a in active:
                if id(a) not in seen and difflib.SequenceMatcher(None, a["norm"], norm).ratio() >= OCR_SAME:
                    hit = a
                    break
            if hit is None:
                hit = {"s": t, "e": t + 1.0 / fps, "norm": norm, "reads": []}
                active.append(hit)
            hit["e"] = t + 1.0 / fps
            hit["reads"].append((text, conf))
            seen.add(id(hit))
        for a in list(active):
            if id(a) not in seen and t - a["e"] > 1.5 / fps:
                active.remove(a)
                done.append(a)
    done.extend(active)
    out = []
    for a in sorted(done, key=lambda x: x["s"]):
        best = max(a["reads"], key=lambda r: (r[1], len(r[0])))
        n = len(a["reads"])
        if n >= OCR_MIN_SAMPLES or best[1] >= OCR_SURE:
            out.append({"s": round(a["s"], 2), "e": round(a["e"], 2), "text": best[0],
                        "conf": round(best[1], 1), "samples": n})
    return out


def ocr_lines(media, start, dur, src_w, src_h, avoid_px=None, frames=None, ocr=None, run=subprocess.run):
    """-> (lines, info). Times are on the clip clock (0 = `start`). `frames`/`ocr` are injectable for tests."""
    info = {"available": False, "frames": 0, "lines": 0, "band_px": list(band_px(src_w, src_h))}
    if ocr is None:
        if not tesseract_available():
            info["error"] = "tesseract is not installed on this machine"
            return [], info
        ocr = tesseract_frame
    x, y, w, h = band_px(src_w, src_h)
    if frames is None:
        cmd, (w, h) = ocr_cmd(media, start, dur, src_w, src_h)
        p = run(cmd, capture_output=True)
        if p.returncode != 0:
            info["error"] = "ffmpeg band decode failed: " + p.stderr.decode(errors="replace")[-200:]
            return [], info
        raw = np.frombuffer(p.stdout, dtype=np.uint8)
        frames = [raw[i * w * h:(i + 1) * w * h].reshape(h, w) for i in range(len(raw) // (w * h))]
    samples = []
    try:
        for i, g in enumerate(frames):
            samples.append((i / OCR_FPS, ocr(blank(g, avoid_px, (x, y)))))
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        return [], info
    lines = merge_lines(samples)
    info.update({"available": True, "frames": len(samples), "lines": len(lines)})
    return lines, info


# ---------- rescue ASR for speech the word list does not cover ----------
def uncovered(words, spans, pad=cv.PAD, min_len=RESCUE_MIN_LEN, join=0.4):
    """Speech seconds (VAD spans) with no caption word on them -> [(a, b)] chunks, near ones joined."""
    iv = sorted((w["s"] - pad, w["e"] + pad) for w in words)
    chunks = []
    for s, e in spans:
        cur = s
        for a, b in iv:
            if b <= cur:
                continue
            if a >= e:
                break
            if a > cur:
                chunks.append([cur, min(a, e)])
            cur = max(cur, b)
            if cur >= e:
                break
        if cur < e:
            chunks.append([cur, e])
    merged = []
    for a, b in chunks:
        if merged and a - merged[-1][1] <= join:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a >= min_len]


def _inside_word(mid, words, tol=0.05):
    return any(w["s"] - tol <= mid <= w["e"] + tol for w in words)


def rescue(pcm, words, spans, transcribe_fn, sr=SR, ctx=RESCUE_CTX, max_sec=RESCUE_MAX_SEC):
    """Re-hear the uncovered speech chunks with VAD off. transcribe_fn(float32 audio) -> [{'w','s','e','p'}] relative
    to the audio. A word is taken only when it sits inside the chunk, on no existing word, p >= RESCUE_MIN_P, and is
    not a known silence hallucination on a short chunk."""
    chunks = uncovered(words, spans)
    total = sum(b - a for a, b in chunks)
    info = {"chunks": [[round(a, 2), round(b, 2)] for a, b in chunks], "chunk_sec": round(total, 2), "added": 0,
            "dropped": []}
    if not chunks:
        return [], info
    if total > max_sec:
        info["error"] = f"{total:.1f} s of uncovered speech is more than the {max_sec:g} s rescue budget"
        chunks = chunks[:max(1, int(max_sec // 1.5))]
    n_total = len(pcm) / sr
    out = []
    for a, b in chunks:
        s0, s1 = max(0.0, a - ctx), min(n_total, b + ctx)
        for w in transcribe_fn(pcm[int(s0 * sr):int(s1 * sr)]):
            ts, te = w["s"] + s0, w["e"] + s0
            mid, word = (ts + te) / 2, w["w"]
            why = None
            if not (a - 0.05 <= mid <= b + 0.05):
                why = "outside the uncovered chunk"
            elif _inside_word(mid, words + out):
                why = "on an existing word"
            elif w.get("p", 1.0) < RESCUE_MIN_P:
                why = f"p={w.get('p', 0):.2f} < {RESCUE_MIN_P}"
            elif (b - a) < 1.2 and cv.tok(word) in HALLUCINATIONS:
                why = "silence hallucination on a short chunk"
            if why:
                info["dropped"].append({"w": word, "t": round(ts, 2), "why": why})
                continue
            out.append({"w": word, "s": round(ts, 3), "e": round(te, 3), "p": w.get("p", 0.5), "rescued": True})
    info["added"] = len(out)
    return sorted(out, key=lambda x: x["s"]), info


def whisper_fn(model, language="en", beam=5):
    """faster-whisper model -> transcribe_fn for rescue(): VAD off, the thresholds that drop quiet speech relaxed."""
    def fn(audio):
        segs, _ = model.transcribe(audio, language=language, beam_size=beam, word_timestamps=True,
                                   vad_filter=False, condition_on_previous_text=False,
                                   no_speech_threshold=None, log_prob_threshold=None)
        return [w for seg in transcribe.collect(segs) for w in seg["words"]]
    return fn
