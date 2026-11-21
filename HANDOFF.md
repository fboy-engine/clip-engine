# HANDOFF

Session started 2026-11-21 16:13 UTC (commit 3b8d64f). Repo reached through the Composio GitHub connector (fboy-engine only); code is built and tested in the Composio workbench (no cv2 there: libGL missing, so vision/render code cannot run there).

## DONE
- Stage 0: git identity + first push, commit 3b8d64f.
- Stage 1 (found already on main, commit 4dd8dfb): Anton + OFL.txt bundled, caption lane on the seam, f*** mask, OCR-reference fix, hook rules, text-over-text gate.
- Stage 2, unit 1: clip_engine/exclusion.py (VOD LHoHInbPkk0 zones, safe crop range 397..1730, clamp, cut_stretches, overlay_gate) + tests/test_exclusion.py. Tests: 210 passed, 1 skipped (baseline was 192 + 1 skipped). Commit sha: see the next HANDOFF update (this file is committed together with the work).

## IN PROGRESS
- Stage 2 unit 2: wire exclusion.py into clip_engine/render_clip.py (plan_cut ~line 240 and render ~line 341: replace cam_layout.crop_bounds with exclusion.crop_left_bounds when the VOD has zones; run overlay_gate on the per-frame boxes and fail the render on a hit; write results into layout.json).

## NEXT
1. Stage 2 unit 2: wire exclusion + overlay gate into render_clip.py; write layout.json (cam rect, top block 1080x540, crop rect, scale factors, exclusion check results).
2. Stage 2 unit 3: cam detection: seed rect + face verify on sampled frames; remove the "wide" detector in vision.py (wide_face, cam_diag); search left/right/mid-edge; refuse the clip when no cam verified (no full-frame fallback); enlarge chain lanczos + light denoise + light sharpen; log scale factor in render.json.
3. Stage 3: tracking (detect >=85%, inside >=95%, hold through gaps, smooth pan), dead-stretch trim + payoff ending, large-v3 on the final clip, run summary (sha256, resolution, bitrate), layout.json in the artifact.

## KNOWN BREAKS
- Last real run 4488260530 (commit 0d476ea): render exited 1, opening-freeze gate FAIL (motion probe measured the whole 16:9 frame while only a 607 px strip is shown). No facecam was found in any corner. See STATUS.md.
- Commit 717ad3c leaked a personal email in author/committer. History not rewritten (needs the user's OK).
- vision.py still has the "wide" detector (100% false matches) until Stage 2 unit 3.
- Not yet verified on a real render: nothing from Stage 2/3 has run on video yet.

## RUN STATE
- Last run: 4488260530, render exit 1 (opening freeze). No run this session yet.
