"""Transcription: faster-whisper (int8, CPU) + Silero VAD (bundled with faster-whisper).

Every run measures wall time, peak memory and model size so the model choice
rests on numbers, not guesses. faster_whisper is imported lazily: the pure
helpers below are unit-tested without it. Runner-agnostic (plain Python).
Words come out as {"w", "s", "e", "p"} (seconds relative to the window start),
the shape captions.to_ass() already takes.
"""
import json
import os
import resource
import time

SR = 16000
DEFAULT_CACHE = os.path.expanduser("~/.cache/clip-engine/models")


def peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # Linux: KB


def dir_size_mb(path):
    """Size of real files under path (symlinks into HF blobs followed)."""
    seen, total = set(), 0
    for root, _, files in os.walk(path):
        for f in files:
            real = os.path.realpath(os.path.join(root, f))
            if real in seen:  # HF cache: blob + snapshot symlink = ONE file
                continue
            seen.add(real)
            try:
                total += os.path.getsize(real)
            except OSError:
                pass
    return total / 1e6


def slice_audio(audio, start=0.0, end=None, max_sec=None, sr=SR):
    """Window [start, end) in seconds, optionally capped to max_sec long."""
    s = max(0, int(start * sr))
    e = len(audio) if not end else min(len(audio), int(end * sr))
    if max_sec:
        e = min(e, s + int(max_sec * sr))
    return audio[s:e]


def collect(segments):
    """Consume a faster-whisper segment generator into plain dicts."""
    out = []
    for seg in segments:
        words = [{"w": (w.word or "").strip(), "s": round(w.start, 3), "e": round(w.end, 3),
                  "p": round(getattr(w, "probability", 0.0) or 0.0, 3)}
                 for w in (seg.words or []) if (w.word or "").strip()]
        out.append({"s": round(seg.start, 3), "e": round(seg.end, 3),
                    "text": seg.text.strip(), "words": words})
    return out


def vad_stats(audio, get_ts=None, sr=SR):
    """Silero VAD speech spans in seconds + total speech seconds."""
    if get_ts is None:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        def get_ts(a):
            return get_speech_timestamps(a, VadOptions())
    spans = [(t["start"] / sr, t["end"] / sr) for t in get_ts(audio)]
    return spans, sum(e - s for s, e in spans)


def transcribe_audio(model, audio, language=None, beam=1):
    """Run a loaded model over audio. Returns (segments, info). VAD filter on."""
    segs, info = model.transcribe(
        audio, language=language, beam_size=beam, word_timestamps=True,
        vad_filter=True, condition_on_previous_text=False)
    return collect(segs), info


def run(media, model_name, start=0.0, end=None, max_sec=None, language=None, beam=1,
        cache_dir=DEFAULT_CACHE, out_dir="out", compute_type="int8"):
    import faster_whisper
    from faster_whisper import WhisperModel, decode_audio
    from faster_whisper.utils import download_model

    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    m = {"model": model_name, "compute_type": compute_type, "device": "cpu",
         "cpu_count": os.cpu_count(), "beam": beam,
         "faster_whisper": getattr(faster_whisper, "__version__", "?")}
    t = time.monotonic()
    audio = decode_audio(media, sampling_rate=SR)
    m["decode_sec"] = round(time.monotonic() - t, 2)
    m["file_audio_sec"] = round(len(audio) / SR, 1)
    audio = slice_audio(audio, start, end, max_sec)
    m["audio_sec"] = round(len(audio) / SR, 1)

    t = time.monotonic()
    spans, speech = vad_stats(audio)
    m["vad_sec"] = round(time.monotonic() - t, 2)
    m["speech_sec"] = round(speech, 1)
    m["speech_ratio"] = round(speech / max(m["audio_sec"], 1e-6), 3)

    before = dir_size_mb(cache_dir)
    t = time.monotonic()
    path = download_model(model_name, cache_dir=cache_dir)
    m["download_sec"] = round(time.monotonic() - t, 2)
    m["cache_hit"] = dir_size_mb(cache_dir) - before < 1.0
    m["downloaded_mb"] = round(dir_size_mb(cache_dir) - before, 1)
    m["model_mb"] = round(dir_size_mb(path), 1)

    t = time.monotonic()
    model = WhisperModel(path, device="cpu", compute_type=compute_type,
                         cpu_threads=os.cpu_count() or 2)
    m["load_sec"] = round(time.monotonic() - t, 2)

    t = time.monotonic()
    segments, info = transcribe_audio(model, audio, language, beam)
    m["transcribe_sec"] = round(time.monotonic() - t, 2)
    m["rtf"] = round(m["transcribe_sec"] / max(m["audio_sec"], 1e-6), 3)  # <1 = faster than realtime
    m["peak_rss_mb"] = round(peak_rss_mb(), 0)
    m["language"] = getattr(info, "language", None)
    m["language_prob"] = round(getattr(info, "language_probability", 0.0) or 0.0, 3)
    m["n_segments"] = len(segments)
    m["n_words"] = sum(len(s["words"]) for s in segments)

    safe = model_name.replace("/", "_")
    with open(os.path.join(out_dir, f"transcript_{safe}.json"), "w") as f:
        json.dump({"window_start": start, "speech_spans": spans, "segments": segments}, f)
    with open(os.path.join(out_dir, f"metrics_{safe}.json"), "w") as f:
        json.dump(m, f, indent=1)
    return m, segments
