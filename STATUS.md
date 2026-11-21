# STATUS

_Updated 2026-11-21. Editing phase only, dry_run=true, nothing posts. Source window is cached (cache_only=true, never re-download)._

## State
- Clip #1 (stream 1242-1280 s of LHoHInbPkk0, cached window 1200-1500) was rendered in run 44873982367 and reviewed. Review found 11 problems; this file tracks the fixes.
- Render pipeline exists (render_clip, grade_clip, grade_checks, cam_layout, track, cropplan, caption_lane, captions, hooks, asr_fix, select_clean). It outputs a full-height 9:16 slice, not cam-on-top.

## Review findings -> code state (before this round)
| # | Finding | State |
|---|---------|-------|
| 1 | 1080x1920, CRF17, bt709, source fps, faststart | encode already set in render_clip/config; review JPEGs are 540 wide (grade_clip.review_frames), not the MP4. Re-verify bitrate/range in the grade report |
| 2 | cam block on top + gameplay panel | NOT built (cam only excluded from crop) |
| 3 | subject tracking + 95% gate | motion centroid only, no gate |
| 4 | dead opening / freeze gate | whole-clip motion gate only; no first-3 s check, no first-motion start |
| 5 | ending on payoff, short fade | select_clean trims; no payoff re-pick; fade is 12 ms |
| 6 | caption accuracy | large-v3 on final clip + asr_fix exist; no OCR reference, no confidence gate |
| 7 | caption lane / overlap gate | fixed margins only; no detection, no gate |
| 8 | hook matches the clip | pick_hook exists; no match check |
| 9 | profanity / sensitive | nothing. Policy decided: mask f*** in captions, audio unchanged, flag in report |
| 10 | audio balance | two-pass loudnorm only; no level matching/limiter, no voice-vs-game report |
| 11 | cookies + one backoff retry | done (cookies input, 30 s backoff + one retry) |

## Plan
A status (this commit) -> B layout/tracking/audio + RUN 1 -> C start/end/freeze -> D captions/lane/hook/profanity + RUN 2.
Nothing below is claimed as passing until a real run shows it.

## CI
- unit-tests green on main before this round (95 passed, 1 skipped).
