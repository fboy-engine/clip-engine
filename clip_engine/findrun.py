"""Glue for `cli find`: signals -> windows -> cheap ASR on candidates only -> gates -> report."""
import json
import os
import time

from . import analyze, find, gemini, transcribe

SR = transcribe.SR


def transcribe_windows(audio, wins, model_name, cache_dir):
    """tiny/small on the candidate windows only. Returns {window.start: segments}; sets w.text."""
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    path = download_model(model_name, cache_dir=cache_dir)
    model = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 2)
    segs_by = {}
    for w in wins:
        chunk = audio[int(w.start * SR):int(w.end * SR)]
        segs, _ = transcribe.transcribe_audio(model, chunk, "en", 1)
        w.text = " ".join(s["text"] for s in segs).strip()
        segs_by[w.start] = segs
    return segs_by


def run(media, offset, model_name, top, out_dir, cache_dir, gen=gemini.generate):
    os.makedirs(out_dir, exist_ok=True)
    t = time.monotonic()
    audio = transcribe.decode_pcm(media)
    db = analyze.rms_db(audio)
    print(f"audio: {len(db)} s decoded in {time.monotonic() - t:.1f}s", flush=True)
    t = time.monotonic()
    spans, speech = transcribe.vad_stats(audio)
    sp = analyze.speech_per_sec(spans, len(db))
    print(f"vad: speech {speech:.0f}s of {len(db)}s in {time.monotonic() - t:.1f}s", flush=True)
    t = time.monotonic()
    motion = analyze.decode_motion(media)
    print(f"motion: {len(motion)} s analysed in {time.monotonic() - t:.1f}s", flush=True)
    wins = sorted(analyze.build_windows(db, sp, motion, spans), key=lambda w: -w.heat)[:top]
    print(f"candidate windows: {len(wins)}", flush=True)
    if not wins:
        print("FAIL find: no candidate windows (flat audio and motion); nothing to render")
        return 1
    t = time.monotonic()
    segs_by = transcribe_windows(audio, wins, model_name, cache_dir)
    print(f"asr {model_name} on {len(wins)} windows in {time.monotonic() - t:.1f}s", flush=True)
    cands = find.evaluate(wins, offset, gen,
                          frames_fn=lambda c: find.extract_frames(media, c["start"], c["end"]))
    for c in cands:
        c["segments"] = segs_by.get(c["start"], [])
    with open(os.path.join(out_dir, "candidates.json"), "w") as f:
        json.dump({"offset": offset, "model": model_name, "candidates": cands}, f)
    print("ALL CANDIDATES (best first)")
    for c in cands:
        print(f'  id={c["id"]} file {c["start"]:.0f}-{c["end"]:.0f}s gate={c["gate"]} '
              f'combined={c["combined"]} local={c["local_score10"]} {"; ".join(c["reasons"])}')
    text = find.report(cands, offset, 3)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("### Top candidates\n```\n" + text + "\n```\n")
    if not any(c["gate_ok"] for c in cands):
        print("NOTE find: no candidate passed the payoff gate; listing the best failures only")
    return 0
