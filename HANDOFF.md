# HANDOFF

Repo fboy-engine/clip-engine, ONLY Composio account github_corral-fowler (login fboy-engine), noreply identity, small commits, push after each unit, update this file each time. Code is built and tested in the Composio workbench (no cv2/ffmpeg there: logic tests run, real video paths do not).

## USER-CONFIRMED (run 44924213343, commit 8a5e730): do not change
Kai block at the top (1080x540 from the 360x180 cam crop), nothing leaks into the gameplay panel, tracking look, the picked moment.

## DONE
- Stages 0-2.3 (see git log; 8a5e730 = Stage 2.3 cam verify).
- Unit A1 (true peak): limiter ceiling -3.0 dBFS (config.LIMITER_CEIL_DB), final ebur128 true-peak check on the finished clip with up to 3 audio-only re-encodes (-1 dB each, video copied), render refuses the clip if still > -1.5; grader TP gate now fails above -1.5 (was -1.0); render.json has audio_final. Tests 234 passed, 1 skipped. Commit a860ca4.
- Unit A2 (captions on the seam): root cause was render() building the ASS with caption_lane.lanes(False) before the cam was verified (seam_y None -> bottom lane). New render_clip.build_ass(cw, hook, use_cam, rect) runs AFTER the cam check with cam_top=use_cam: every caption is \\an5\\pos(540,540). Tests 236 passed, 1 skipped.

## RUN INPUTS (verification runs, workflow clip.yml, ref main, account github_corral-fowler)
url https://www.youtube.com/watch?v=LHoHInbPkk0, start_sec 1200, end_sec 1500, render_start 45, render_end 85, cache_only true, render true, dry_run true, warp false, cookies false, hook_text and cam_rect empty. Source cache key src-3cb28dbf11cce196-v1. Stage A verification run on b42d7dea was started by the user (they read the result).

## STAGE B STATE
- B1 done (42bd96d): clip_engine/quality.py (psnr, ssim, blockiness, edge_strength, mean_gradient).
- B2 (this commit): cam_sr.py (4 upscalers: lanczos, OpenCV dnn_superres FSRCNN_x3 / EDSR_x3, Real-ESRGAN general-x4v3 ONNX -> area 3x; model files SHA-256 pinned; time/frame measured first; choose() = best SSIM that fits SR_BUDGET_SEC=720 at cam 30 or 24 fps AND does not lose to lanczos on PSNR/SSIM/blockiness), cam_bench.py + `cli cam-bench` (REAL crops from the cached source via seeded_cam + face path; deblock variants scored on the same crops; cam_bench.json/.md + cam_compare.png), srcinfo.py (source format/bitrate, metadata-only format listing, better_formats), workflow: input cam_bench, opencv-contrib-python-headless>=4.9,<5 + onnxruntime, SR model cache, bench step, artifact cambench-<run>.
- Finding: the static ffmpeg build (BtbN) has NO `pp` filter (libpostproc dropped), so deblock candidates are deblock weak/strong, spp, pp7 (chosen by measured blockiness drop with SSIM >= 0.93). Tested end to end locally on a synthetic 1080p source only (not representative): real numbers come from the Actions bench run.
- Sandbox limits: ~400 MB free RAM: EDSR (38 MB .pb) gets OOM-killed here, so EDSR is only exercised in Actions.
- B3a DONE (54c6497, NOT wired into render yet): clip_engine/cam_enhance.py = the whole B3 chain as library code. Pure geometry (roi_union, block_matrix = sub-pixel 1080x540 crop with edge-aligned pixels, window_4x, prepass_cmd = ffmpeg fps=cam_fps -> deblock -> crop ROI -> hqdn3d, bgr24 rawvideo). old_block() = the previous render chain (bilateral 5/20/5 + lanczos4 + unsharp 0.35/1.2), real_small() = untouched crop as fidelity reference, block_metrics() = {fidelity (SSIM of block shrunk back vs real crop), blockiness on the source 8 px grid as it lands in the block, edge (p95 gradient)}. sr_mix() = Real-ESRGAN 4x blended with lanczos 4x by SR share. CamStream(media,start,dur,fps,boxes,roi,plan,up,fps_arg) = sequential producer, block(i) for i=0,1,2...: SR only on prepass frames (cam_fps), output frames in between by DIS optical flow warp both ways with forward-backward reliability map (unreliable pixels take the nearest frame), plan interp='nearest' fallback = nearest SR frame + 0.3 of the real-frame lanczos detail; then to_block() (sub-pixel crop) + cam_sr.finish() (unsharp 0.25/1.0 + luma grain 1.1). info() gives sr_frames, sr_seconds, flow_seconds. ghost_test() runs the real chain on the most moving 1.5 s and compares keyframe vs in-between fidelity against the real frames. cam_sr.py additions: B3_FPS_TIERS (20,15,12,10,8), BLEND_STEPS (1.0..0.25), BLEND_START 0.7, FIDELITY_MIN 0.96, choose_b3(), gate_numbers(), choose_blend(), ghost_verdict() (mean drop <= 0.03, worst <= 0.08), plan_cam_fps(sec_per_frame, dur, cap_fps) = per-clip fps fit with the 720 s SR budget and 1.25 safety. tests/test_cam_enhance.py, tests/test_cam_sr.py.
- B3b DONE (08a6047): cam_bench.run() has a B3 section: Real-ESRGAN chosen via choose_b3, SR share measured on 4 real ROI frames over BLEND_STEPS against the OLD chain (cam_enhance.old_block), result in cam_bench.json key "b3" and a B3 table in cam_bench.md, cam_compare.png = OLD over NEW at the real 1080x540 size on two moments. Still the bench only: render_clip.py does not call cam_enhance yet.
- Bench run 45024407051 (commit 08a6047, artifact cambench-45024407051): Real-ESRGAN 0.92 s/frame on the Actions runner, fits 720 s at 12 fps for a 45 s worst case (15 fps fits for a 40 s clip by plan_cam_fps: 602 frames x 0.92 x 1.25 = 692 s). Deblock none chosen. B3 GATE FAILED at every SR share: fidelity ok (1.0: 0.964, 0.7: 0.981, 0.25: 0.996), edge higher than old at every share (15.8 / 14.0 / 12.6 vs 11.97) but blockiness 1.005 / 1.003 / 0.990 vs OLD 0.911. FINDING: the OLD chain wins blockiness only because its bilateral blur softens the 8 px grid lines to 0.911 (<1.0 means weaker grid than the neighbouring pixels = smoothing, not cleaner). Every SR share has blockiness about 1.0 = no grid. So "blockiness lower than lanczos" rewards blur. Decide in B3d how the gate treats this and say it in the run summary.
- B3c/d/e/f TODO (in order): B3c wire into render (cam_quality in render.json), B3d gate + tests, B3e cam_compare.png + source/clip.mp4 info in run summary, B3f verification run.
- B4 TODO: gameplay panel.

## NEXT (user order)
A2 done (see DONE). Stage A verification run started by the user on b42d7dea.
B (IN PROGRESS, units: B1 quality.py metrics, B2 cam_sr.py + cam_bench step + cam_compare.png, B3 render chain + grader gate + source format/bitrate, B4 gameplay panel). Kai block quality (TOP priority, size fixed 1080x540 from 360x180): deblock (pp/deblock) -> hqdn3d low -> real upscaler chosen by measurement (lanczos baseline, OpenCV dnn_superres FSRCNN/EDSR, Real-ESRGAN-general ONNX CPU; PSNR/SSIM + blockiness; time/frame benchmark first; 35 min step limit; 30 fps cam with frame duplication allowed) -> gentle unsharp + very light grain. No face restoration. Gate enhanced > plain lanczos; cam_compare.png in artifact; log source format/bitrate in the run summary; do NOT re-download.
C. Gameplay panel: one lanczos/accurate_rnd/full_chroma_int scale, light cas/unsharp, deband; sharpness gate vs plain-scale baseline (replace median 9.2).
D. Captions match the voice: every spoken word, word-timed, one style; OCR of game subtitles + Gemini audio transcription as second source (prefer OCR for game dialogue); hook = complete phrase <=6 words, never splits a sentence with caption 1; captions_check.json; gates coverage >=90% of speech seconds and no unresolved source disagreement; keep f*** mask.
E. Tracking detection rate 62-65% -> >=85% as a gate, same look.
F. Finder untouched; save docs/reference_clip.json and use as tie-breaker only.
G. Run summary: sha256, resolution, bitrate of clip.mp4; keep layout.json.
After each stage: commit sha to the user, ask for a cache_only=true verification run (or start it if the connector allows).

## KNOWN BREAKS
- Commit 717ad3c and af9a12f leaked a personal email; history NOT rewritten (needs the user's OK).
- Overlay gate checks planned geometry, not rendered pixels.

## STEP 0 (done)
- Bench run 44998778975 failed with 0 samples because render_start/render_end were both 0. Fix: clip_engine/window.py check_window() (0/0, equal, reversed, <4 s, start past media = clear ValueError naming render_start/render_end); used by cam-bench (before and inside cam_bench.run) and render; cam-bench always writes out/cam_bench.json + .md with the error before exiting 1. Tests: tests/test_window.py.
- Correct bench run 45023100430 (artifact cambench-45023100430): lanczos won on time budget (fsrcnn not better, edsr 19.2 s/frame, realesrgan 0.59 s/frame too slow at cam 30 fps); deblock none chosen; source h264 1920x1080 60 fps ~5433 kbps; format listing needs cookies (report only).
- USER DECISION (B3 go): Real-ESRGAN general-x4v3 on the cam region at cam_fps 15 (try 20), in-between frames by motion-compensated blend / optical-flow warp with a no-double-edge check (fallback 20 fps or nearest SR frame + lanczos detail), SR/lanczos blend ~70/30 chosen by measurement, gate face fidelity >= 0.96, blockiness < lanczos, edge > lanczos, auto-lower SR fps for long clips, sr_seconds in render.json, cam_compare.png old vs new at 1080x540, deblock chosen per source by measurement, review 3 frames (face, mid-motion, purple tint).

## RUN STATE
- Last user-reviewed run: 44924213343 on 8a5e730. Bench run 45024407051 on 08a6047 (see Stage B state). Composio workbench has numpy but no cv2/ffmpeg: real video paths are only exercised in Actions.
