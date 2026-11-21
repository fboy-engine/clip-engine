# HANDOFF

Session started 2026-11-21 16:13 UTC (commit 3b8d64f). Repo reached through the Composio GitHub connector (fboy-engine only); code is built and tested in the Composio workbench (no cv2 there: libGL missing, so vision/render code cannot run there; geometry and logic tests do).

## DONE
- Stage 0: git identity + first push, commit 3b8d64f.
- Stage 1 (already on main, commit 4dd8dfb): Anton + OFL.txt bundled, caption lane on the seam, f*** mask, OCR-reference fix, hook rules, text-over-text gate.
- Stage 2.1, commit 00a6f1e: clip_engine/exclusion.py (VOD LHoHInbPkk0 zones, safe crop range 397..1730, clamp, cut_stretches, overlay_gate) + tests.
- Stage 2.2 (this commit, sha in the next update): render_clip uses exclusion zones for the crop bounds (panel_bounds, refuses when the crop cannot fit), runs the overlay gate on every frame's crop and fails the render on a hit, writes layout.json (cam rect, top block 1080x540, panel 1080x1380, crop rect, panel scale, cam upscale, exclusion results); CLI --vod-id; workflow passes the stream URL as --vod-id and uploads layout.json. Tests: 219 passed, 1 skipped (baseline 192 + 1).

## IN PROGRESS
- nothing half-done. Stage 2.3 not started.

## NEXT
1. Stage 2.3: cam detection: seed rect (0,432,392,653 on the VOD) + face verify on sampled frames; remove the "wide" detector in vision.py (wide_face, its use in cam_diag/faces_in_rect); search left, right and mid-edge, not only the 4 corners; refuse the clip when no cam is verified (no full-frame fallback; render.py currently falls back to gameplay-only); enlarge chain lanczos + light denoise + light sharpen in render_frames; log the scale factor in render.json.
2. Stage 3: tracking (detect >=85%, inside >=95%, hold through gaps, smooth pan), dead-stretch trim + payoff ending (opening motion must be measured on the shown strip), large-v3 on the final clip, run summary (sha256, resolution, bitrate of clip.mp4).
3. Stage 4: verification run with cache_only=true on key src-3cb28dbf11cce196-v1 (workflow_dispatch needs GitHub Actions access, not available through this connector: ask the user to trigger it or give a way).

## KNOWN BREAKS
- Last real run 4488260530 (commit 0d476ea): render exited 1, opening-freeze gate FAIL (motion probe measured the whole 16:9 frame while only a 607 px strip is shown); no facecam found in any corner. Nothing from Stage 2 has run on real video yet.
- Commit 717ad3c leaked a personal email in author/committer. History not rewritten (needs the user's OK).
- vision.py still has the "wide" detector (100% false matches) until Stage 2.3; render still falls back to gameplay-only when no cam is verified (should refuse).
- The overlay gate checks the planned crop geometry of every frame (not rendered pixels); a pixel-level check of the finished clip is not written yet.

## RUN STATE
- Last run: 4488260530, render exit 1 (opening freeze). No run this session.
