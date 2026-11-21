# STATUS

_Updated 2026-11-21. Editing phase only, dry_run=true, nothing posts. Source window is cached (cache_only=true, never re-download)._

## State
- Last real run: 44879350720 FAILED at render: `cv2` has no `CascadeClassifier` (unpinned pip pulled OpenCV 5.1.0). No clip produced.
- Commit A (this one): opencv pinned `>=4.9,<5` in clip.yml; vision.py loads detectors through `load_face_cascade` / `load_people_detector` (tries CascadeClassifier, objdetect.CascadeClassifier, xobjdetect.CascadeClassifier; raises `DetectorUnavailable` with a clear message); new `check-cv` CLI step runs before the render so a bad install fails in seconds.
- Unit tests: 121 passed, 1 skipped locally (synthetic, no video). A real run has NOT yet confirmed the pin works.

## Review findings (first clip, run 44873982367) -> state
| # | Finding | State |
|---|---------|-------|
| 1 | 1080x1920 CRF17 bt709 | encode set; needs real-run grade numbers |
| 2 | cam on top + gameplay panel | code exists (cam_layout, layout, render_clip); unverified on real video |
| 3 | subject tracking + 95% gate | code exists (subject, track, gate); unverified |
| 4 | dead opening / freeze gate | to wire in B |
| 5 | payoff ending, 25-45 s | to wire in B |
| 6 | caption accuracy (OCR ref, confidence gate) | to do in C |
| 7 | caption lane + overlap gate | to do in C |
| 8 | hook matches first seconds | to wire in B |
| 9 | profanity mask f***, sensitive note | to do in C |
| 10 | audio balance, LUFS/LRA/peak report | code exists (balance); unverified |
| 11 | cookies input + one backoff retry | in clip.yml/download; verify in D |

## Plan
A (done, pushed) -> B first-motion start, freeze gate, payoff end, hook + RUN 1 -> C captions + RUN 2 -> D cookies check.
Nothing is claimed as passing until a real run shows it.
