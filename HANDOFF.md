# HANDOFF

Session started 2026-11-21 16:13 UTC (commit 3b8d64f). Repo reached through the Composio GitHub connector (fboy-engine only); code is built and tested in the Composio workbench (no cv2 there: libGL missing, so cv2/ffmpeg code paths CANNOT be run here; geometry and logic tests do).

## DONE
- Stage 0: git identity + first push, commit 3b8d64f.
- Stage 1 (already on main, commit 4dd8dfb): Anton + OFL.txt bundled, caption lane on the seam, f*** mask, OCR-reference fix, hook rules, text-over-text gate.
- Stage 2.1, commit 00a6f1e: clip_engine/exclusion.py (VOD LHoHInbPkk0 zones, safe crop range 397..1730, clamp, cut_stretches, overlay_gate).
- Stage 2.2, commit 2ba1b47: crop bounds from the zones (render refuses when the crop cannot fit), per-frame overlay gate fails the render, layout.json (cam rect, top block 1080x540, panel 1080x1380, crop rect, panel scale, cam upscale, exclusion results), CLI/workflow --vod-id, layout.json uploaded.
- Stage 2.3 (this commit, sha in the next update): "wide" detector removed (wide_face, _extra_cascades, all wide counters); cam verified by the strict detector + vision.verify_cam (large face >= 12% of the cam region width, >= 35% of samples, stable); the cam zone of a known VOD seeds the check; search regions now include mid-left, mid-right, top-mid, bottom-mid; no verified cam -> the render RAISES (no gameplay-only / full-frame fallback; only an explicit cam_corner=none on a VOD without zones still allows it); cam enlarge chain = bilateral denoise on the source crop, lanczos4 upscale, light unsharp; scale factor in render.json (cam_scale) and layout.json. Tests: 230 passed, 1 skipped (baseline 192 + 1).

## IN PROGRESS
- nothing half-done. Stage 3 not started.

## NEXT
1. Stage 3a: tracking: detect the main character in >=85% of frames, keep inside the crop in >=95%, hold through gaps, smooth the pan (clip_engine/subject.py, track.py; use exclusion.cut_stretches to trim/skip stretches where the clamped crop would cut the character). Opening/start motion must be measured on the SHOWN strip (shown_opening exists; make pick_start use the same crop).
2. Stage 3b: dead-stretch trim (no character visible) + payoff ending, keep frame-accurate trim; large-v3 on the final clip with the OCR reference; run summary with sha256 / resolution / bitrate of clip.mp4; layout.json already in the artifact.
3. Stage 4: verification run with cache_only=true on key src-3cb28dbf11cce196-v1 (GitHub Actions workflow_dispatch is not reachable through this connector: the user must trigger it, or give a way). Then read the run artifact and report run id + commit.

## KNOWN BREAKS
- NOTHING from Stage 2 has run on real video: the render path (cv2 face check, denoise, warp, ffmpeg) is untested end to end. The first real run (Stage 4) will show it.
- Last real run 4488260530 (commit 0d476ea): render exited 1, opening-freeze gate FAIL (motion probe measured the whole 16:9 frame while only a 607 px strip is shown); no facecam found in any corner.
- Commit 717ad3c leaked a personal email in author/committer. History not rewritten (needs the user's OK).
- The overlay gate checks the planned crop geometry of every frame, not rendered pixels; a pixel-level check of the finished clip is not written yet.
- Tracking thresholds (85% detected / 95% inside) are not enforced as gates yet (Stage 3a).

## RUN STATE
- Last run: 4488260530, render exit 1 (opening freeze). No run this session.
