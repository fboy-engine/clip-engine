import json
import os

import numpy as np

from clip_engine import caption_gaps as cg

SR = 16000


def tone(sec, f0=140.0, amp=0.2, am_hz=4.0):
    t = np.arange(int(sec * SR)) / SR
    env = 0.5 * (1 + np.sin(2 * np.pi * am_hz * t))
    return (amp * env * np.sin(2 * np.pi * f0 * t) * (1 + 0.5 * np.sin(2 * np.pi * 280 * t))).astype(np.float32)


def noise(sec, amp=0.05, seed=1):
    return (amp * np.random.RandomState(seed).randn(int(sec * SR))).astype(np.float32)


def test_voiced_share_separates_voice_from_noise():
    assert cg.voiced_share(tone(1.5))[0] > 0.6
    assert cg.voiced_share(noise(1.5))[0] < 0.2


def test_spectral_flatness_noise_high_tone_low():
    _, fn = cg.spectral_stats(noise(1.0))
    _, ft = cg.spectral_stats(tone(1.0))
    assert fn > ft


def test_features_digital_silence():
    f = cg.features(np.zeros(SR, dtype=np.float32))
    assert f["silent_frame_share"] == 1.0
    assert cg.hint(f, [], 1.0)[0] == "digital silence"


def test_timing_quality_counts_collapsed_and_smeared():
    ws = [{"w": "a", "s": 0.0, "e": 0.01}, {"w": "b", "s": 1.0, "e": 1.0}, {"w": "get", "s": 2.0, "e": 3.6},
          {"w": "ok", "s": 4.0, "e": 4.3}]
    q = cg.timing_quality(ws)
    assert (q["words"], q["collapsed"], q["smeared"]) == (4, 2, 1)


def test_hint_words_heard_wins_over_audio_numbers():
    h, why = cg.hint({"voiced_frame_share": 0.1, "syllable_modulation": 0.1, "silent_frame_share": 0.0},
                     [{"w": "back", "s": 1.0, "e": 1.2, "p": 0.8}], 1.0)
    assert h.startswith("words heard") and "back" in why[0]


def test_hint_low_p_words_do_not_count_and_no_voice_is_not_speech():
    h, _ = cg.hint({"voiced_frame_share": 0.05, "syllable_modulation": 0.2, "silent_frame_share": 0.0},
                   [{"w": "x", "s": 1.0, "e": 1.1, "p": 0.1}], 1.0)
    assert h.startswith("no pitched voice")


def test_hint_never_names_a_speaker():
    for f in ({"voiced_frame_share": 0.9, "syllable_modulation": 0.6, "silent_frame_share": 0.0},
              {"voiced_frame_share": 0.3, "syllable_modulation": 0.3, "silent_frame_share": 0.0}):
        h, _ = cg.hint(f, [], 1.0)
        assert "streamer" not in h.lower() and "game" not in h.lower().replace("game ", "") or "listen" in h


class FakeSeg:
    def __init__(self, words):
        self.words = words
        self.start, self.end, self.text = words[0].start, words[-1].end, " ".join(w.word for w in words)


class FakeWord:
    def __init__(self, word, start, end, probability=0.9):
        self.word, self.start, self.end, self.probability = word, start, end, probability


class FakeModel:
    def __init__(self, table):
        self.table, self.calls = table, []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        return ([FakeSeg([FakeWord(*w) for w in self.table])] if self.table else []), None


def test_heard_uses_rescue_settings_and_shifts_to_clip_clock():
    m = FakeModel([("back", 0.5, 0.8)])
    out = cg.heard(m, np.zeros(10 * SR, dtype=np.float32), 4.0, 5.0, 1.0)
    assert out == [{"w": "back", "s": 3.5, "e": 3.8, "p": 0.9}]
    k = m.calls[0]
    assert k["vad_filter"] is False and k["no_speech_threshold"] is None and k["log_prob_threshold"] is None
    assert k["beam_size"] == 5 and k["word_timestamps"] is True


def test_projected_only_counts_words_inside_stretches_and_not_on_existing():
    words = [{"w": "a", "s": 0.0, "e": 1.0, "p": 1.0}]
    spans = [(0.0, 3.0)]
    hw = [{"w": "new", "s": 1.6, "e": 2.0, "p": 0.8}, {"w": "dup", "s": 0.2, "e": 0.8, "p": 0.9},
          {"w": "weak", "s": 2.2, "e": 2.4, "p": 0.1}, {"w": "far", "s": 9.0, "e": 9.5, "p": 0.9}]
    p = cg.projected(words, spans, hw, [(1.1, 3.0)])
    assert p["added"] == ["new"] and p["coverage"] > cg.cv.coverage(words, spans)[0]


def test_diagnose_end_to_end_with_fake_loader(tmp_path):
    pcm = np.concatenate([tone(2.0), noise(3.0, 0.02), tone(2.0)])
    spans = [(0.0, 7.0)]
    words = [{"w": "yo", "s": 0.0, "e": 1.9, "p": 0.9}, {"w": "end", "s": 5.2, "e": 6.9, "p": 0.9}]

    def loader(name, cache, ctype):
        return FakeModel([("hey", 0.6, 1.0)])
    rep = cg.diagnose(pcm, spans, words, {"ok": False}, [{"s": 2.0, "e": 4.0, "text": "Lose the cops"}], str(tmp_path),
                      loader=loader, log=lambda *_: None)
    assert rep["stretch_count"] == 1 and rep["stretches"][0]["subtitle_lines_on_screen"] == ["Lose the cops"]
    assert os.path.exists(tmp_path / "captions_gaps.json") and len(os.listdir(tmp_path / "gaps")) == 1
    assert rep["stage"] == "complete" and not rep["skipped_for_time"] and not rep["variant_errors"]
    assert set(rep["projected_coverage_if_taken"]) == set(cg.VARIANTS)
    json.dumps(rep)


def test_diagnose_survives_a_variant_that_cannot_load(tmp_path):
    pcm = np.concatenate([tone(1.0), noise(2.0, 0.02), tone(1.0)])
    words = [{"w": "a", "s": 0.0, "e": 0.9, "p": 0.9}, {"w": "b", "s": 3.1, "e": 3.9, "p": 0.9}]

    def loader(name, cache, ctype):
        if ctype == "float32":
            raise MemoryError("no")
        return FakeModel([])
    rep = cg.diagnose(pcm, [(0.0, 4.0)], words, {"ok": False}, [], str(tmp_path), loader=loader, log=lambda *_: None)
    assert "float32_ctx1.5" in rep["variant_errors"] and rep["stage"] == "partial"
    assert "int8_window" in rep["stretches"][0]["heard"]


def test_budget_skips_variants_but_keeps_the_audio_measurements(tmp_path):
    pcm = np.concatenate([tone(1.0), noise(2.0, 0.02), tone(1.0)])
    words = [{"w": "a", "s": 0.0, "e": 0.9, "p": 0.9}, {"w": "b", "s": 3.1, "e": 3.9, "p": 0.9}]
    ticks = iter(range(0, 10000, 400))                 # every clock read is 400 s later: the 300 s budget is gone at once
    loaded = []

    def loader(name, cache, ctype):
        loaded.append(ctype)
        return FakeModel([])
    rep = cg.diagnose(pcm, [(0.0, 4.0)], words, {"ok": False}, [], str(tmp_path), loader=loader, log=lambda *_: None,
                      clock=lambda: next(ticks))
    assert loaded == [] and rep["stage"] == "partial" and rep["skipped_for_time"]
    saved = json.load(open(tmp_path / "captions_gaps.json"))
    assert saved["stretches"][0]["features"]["mean_dbfs"] is not None and saved["timing_quality"]["words"] == 2


def test_float32_only_starts_with_enough_budget_left(tmp_path):
    pcm = np.concatenate([tone(1.0), noise(2.0, 0.02), tone(1.0)])
    words = [{"w": "a", "s": 0.0, "e": 0.9, "p": 0.9}, {"w": "b", "s": 3.1, "e": 3.9, "p": 0.9}]
    t = {"now": 0.0}
    loaded = []

    def clock():
        return t["now"]

    def loader(name, cache, ctype):
        loaded.append(ctype)
        t["now"] += 200.0                              # the int8 load eats 200 of the 300 s: 100 s left < 150 s
        return FakeModel([])
    rep = cg.diagnose(pcm, [(0.0, 4.0)], words, {"ok": False}, [], str(tmp_path), loader=loader, log=lambda *_: None,
                      clock=clock)
    assert loaded == ["int8"] and any("float32" in s for s in rep["skipped_for_time"])
