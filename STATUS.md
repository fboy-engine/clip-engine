# STATUS

_Updated 2026-11-21 (after reading run 44882605300 artifact). Editing phase only, dry_run=true, nothing posts. Source window is cached (cache_only=true, never re-download)._

## Real result: run 44882605300 (commit 0d476ea)
OpenCV pin worked (`check-cv` passed, detectors loaded). The render step produced `clip.mp4` (43.3 MB) and then **exited 1 because ONE grade check FAILED**, plus one WARN:

| Check | Result | Numbers |
|---|---|---|
| **opening: no freeze, real motion (first 3 s)** | **FAIL** | longest frozen 2.5 s, 0.0% of steps moving, first motion at None s |
| layout: cam on top, bigger gameplay below | WARN | no facecam found: gameplay-only 9:16 (face hits per corner: BL 8.0%, BR 6.7%, TL 8.0%, TR 10.7%; needs >= 35%) |
| video format | pass | 1080x1920 h264 yuv420p, 60 fps, 9056 kbps, bt709 tv, CRF 17 |
| length 25-45 s | pass | 37.37 s, 2242 frames, A/V 37.367 s |
| loudness | pass | -14.0 LUFS, LRA 5.2, true peak -1.4 dBFS |
| voice vs game audio | pass | voice -14.8 dB, loud peaks -8.5 dB, gap 6.3 dB |
| cold open sound | pass | sound at 0.00 s |
| hook | pass | "bro Give" from 0.00 s, matches first 3 s |
| ending | pass | 0.36 s after the last word; payoff by heuristic (phrase not found) |
| captions | pass | 10 lines, 1 style, max 1 highlight |
| subject inside panel >= 95% | pass (soft) | 98.3% inside on only 78.7% detected frames of 75; plan-level tracked 77.3% / inside 58.6% |
| punch-in / pan | pass | punch max 8.0%, pan max 489 px/s |
| visual / motion | pass | 0% bad frames; 89% of seconds moving, longest static 4 s |

ASR: large-v3 on the final window only: 52 s audio, 12.2 s, 48 words. Timings: track 13.8 s, loudnorm 1.0 s, render 227.5 s.

## Diagnosis of the failed gate (measured, not guessed)
- Review frames r0 (0.3 s) and r1 (0.9 s) are pixel-identical (mean diff 0.06); r2 (4.0 s) differs only by caption/hook text plus 0.7% of pixels. The shown picture is frozen for the first ~4 s.
- The cut starts at source 45.05 s = asked 42.0 + 3.05 s "first motion". The trim motion probe ran on the WHOLE 16:9 frame (no facecam rect, so nothing masked), but the output only shows a 607 px wide strip (crop x about 375-982 of 1920). Motion outside that strip counts as "first motion", so the start was chosen where the visible picture is still frozen. The output gate looks only at the visible panel, hence FAIL. This is the likely cause; the fix measures motion in the strip that is actually shown.
- Second symptom of the same dead opening: start is inside the word "bro" (ends 45.08 s), next speech at +2.79 s, so the snap rule (max 1 s) left it mid-word; the clip opens with a 30 ms caption sliver "bro" and the hook text came from it.
- Not a gate bug: thresholds are unchanged and stay unchanged.

## Other things the real clip shows (to fix, none pass-claimed)
- No facecam found in any corner (hits 6.7-10.7%). Cannot tell from the artifact whether the stream had a cam in this window. Next run writes `cam_diag.json` (relaxed per-corner detection counts) so this can be decided from data.
- Captions are not accurate yet: "Female muscular", "in her 20s nah", unmasked profanity, a 10 s gap with no captions (12.6 s to 22.3 s). This is slice C.
- The 95% subject gate passes on 78.7% detected frames; unseen frames count as not-outside. Noted, not changed in this commit.

## Review findings (11) -> state
| # | Finding | State |
|---|---|---|
| 1 | 1080x1920 CRF17 bt709 | verified on real run: pass |
| 2 | cam on top + gameplay panel | code exists; real run found no cam (WARN) |
| 3 | subject tracking + 95% gate | real run: gate pass but weak coverage (78.7% detected) |
| 4 | dead opening / freeze gate | real run FAILED; fix in progress (visible-strip motion) |
| 5 | payoff ending 25-45 s | real run: pass (heuristic) |
| 6 | caption accuracy | todo (C) |
| 7 | caption lane + overlap gate | todo (C) |
| 8 | hook matches first seconds | real run: pass (but derived from a sliver, see above) |
| 9 | profanity mask f*** | todo (C) |
| 10 | audio balance, -14 LUFS | real run: pass |
| 11 | cookies input + one backoff retry | in clip.yml/download; verify in D |

## Plan
Opening fix -> cam diagnostics -> ONE rerun (cache_only=true) -> C (large-v3 final clip only, asr_fix with on-screen subtitle reference, caption lane on the seam + overlap gate, profanity mask) -> D (cookies input + one backoff retry).
Nothing is claimed as passing until a real run shows it.
