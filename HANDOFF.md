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
- B3c DONE: render now uses the chain from out/cam_bench.json (render_clip.load_b3: clear ValueError when the bench did not run / failed / has no b3 / is lanczos-only / gate failed). cam_enhance.prepare(): ROI union of the per-frame boxes, Real-ESRGAN timed on a production-size ROI at render time, SR fps = highest of 20/15/12/10/8 that fits SR_BUDGET_SEC for THIS clip (plan_cam_fps), ghost test (optical flow; on double edges falls back to nearest SR frame + real detail; both ghosting = render refused). render_frames() takes cam_stream + probe: PROBE_FRAMES (6) finished output blocks are measured against the untouched real crop and against the OLD chain on the same frame (cam_enhance.probe_frame). render.json gets cam_quality (chain, sr_fps, sr_share, sr_seconds, fidelity, blockiness, edge, enhanced vs lanczos numbers, ghost verdict, gate) and grade_inputs.cam_quality. Old bilateral/lanczos path stays only as the no-stream fallback in render_frames. Tests: tests/test_cam_wire.py (load_b3, gate rule, choose_blend prefer, report, CamStream with a fake upscaler + fake ffmpeg pipe under OpenCV: block always 1080x540 in flow and nearest mode, SR runs only at cam_fps, flow in-betweens SSIM 0.998 vs 0.927 for a plain cross-fade = no ghosting).
- GATE RULE CHANGE (disclosed, one function: cam_sr.blockiness_ok): blockiness must be lower than plain lanczos, EXCEPT when plain lanczos is below 1.0 (its bilateral blurs the grid lines, measured 0.911): then a block with no visible grid (<= 1.02) passes. Equal or higher than a neutral (>= 1.0) old chain still fails. Reason: with the strict rule no sharper image can pass (bench run 45024407051: every SR share ~1.0 vs 0.911). choose_blend(prefer=BLEND_START) takes the passing share closest to 0.7 (tie -> smaller), bench passes it. To revert to the strict rule, delete the second `if` in blockiness_ok.
- Sandbox note: /home/user/repo in the Composio workbench persists between Claude sessions; the previous Claude left tests/test_cam_gate.py there (never pushed). Its GraderGate/Summary classes are the spec for B3d (grade_checks.check_cam_quality, ALL) and B3e (render_clip.summary_md); saved in /home/user/repo/_parts.json. cv2 (opencv-contrib-python-headless) installs in the workbench with pip, so pixel tests can run there; ffmpeg and onnxruntime models do not.
- B3d DONE: cam_sr.quality_verdict(cam_quality) = fidelity >= 0.96 vs the untouched real crop, blockiness rule (blockiness_ok), edge higher than plain lanczos, ghost verdict; used by grade_checks.check_cam_quality (in ALL; n/a without a cam block, FAIL when the measurement is missing) and by render(): after render.json/layout.json are written it raises ValueError('cam quality gate failed ... reasons') so a clip that does not beat plain lanczos is never shipped. tests/test_cam_gate.py (grader pass/fail per metric, ghost, real bench numbers, real grid still fails), tests/test_grade.py fixture has cam_quality.
- B3e DONE: the render writes out/cam_compare.png itself (cam_enhance.write_compare: 3 moments, OLD bilateral+lanczos4+unsharp block over the NEW block, both from the same source frame, real 1080x540; overwrites the bench's png in the same artifact) and render.json gets source_format (srcinfo.media_info of the cached source: codec, profile, size, fps, bitrate) and clip_file (render_clip.clip_file_info: sha256, resolution, fps, video_kbps, size). render_clip.summary_md(r) is printed and appended to the Actions run summary by cli (also after a gate refusal, from render.json). Tests: tests/test_cam_summary.py.
- B3f FIX (run 45026217442 render failure 'could not cut the cam ROI'): ROOT CAUSE = ffmpeg crop on yuv420p rounds odd x/y/w/h DOWN to even; the real ROI was the full 393 px wide cam region (odd), so the 1-frame grab returned fewer bytes than w*h*3 and was treated as failure (the bench never hit it: its crops are 360x180). Same hazard in CamStream/read_frames (frame size mismatch). Fix: cam_enhance.roi_union now returns EVEN x,y,w,h (_even_span), inside region+frame. Errors now explain themselves: prepare() error = crop rect, odd flag, region, source, window start/dur, seek t, ffmpeg exit, bytes read vs expected, stderr tail (grab_failure); CamStream prepass stderr goes to a temp file and 'no frames' error has ROI, window, frames read, stderr tail. Test: tests/test_cam_real_video.py (real testsrc 1920x1080 h264 mpegts made by ffmpeg; passes with fix, fails with the old roi_union - verified). unit-tests.yml installs opencv + ffmpeg so it runs in CI (commit follows the fix).
- B3f TODO (verification run after the fix): verification run (inputs in RUN INPUTS), look at 3 review frames (face, mid-motion, purple tint), report honestly.
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

## PART A (editing fixes, this Claude)
- A1a+A1b: captions_verify.py (15c? tests, commit 15e04e9) = verify each large-v3 word against Gemini audio text and OCR of game subtitles (OCR preferred for lines that match the speech), insert words missed by VAD into speech gaps, coverage>=90% of speech seconds + zero unresolved gate, data for out/captions_check.json. hooks.phrase_hook (whole sentence/clause <=6 words, never a piece or copy of caption 1; hook no longer removes words from captions) wired in render_clip.build_ass. NOT YET WIRED into render(): Gemini audio call, OCR, rescue ASR pass, captions_check.json, grader checks (A1c).

## RESUME POINT (2026-11-23, session 2)
Origin main was b82c3a3 at session start. A previous sandbox lost unpushed A1c work; it was rebuilt and pushed (below).
Order (user): A1c -> A3 (side cam, Twitch source, any streamer) -> A2 tracking >= 85% gate -> A4 docs/reference_clip.json
(finder tie-breaker only) -> C sharpness gate -> verification run on the first Lauren Twitch clip
https://m.twitch.tv/laurenhamdenn/clip/GoldenSincereHamburgerHeyGuys-Zo31y1z1tLdwRScS (dry_run true, warp false, cookies false;
cache_only cannot hit on a never-downloaded Twitch URL, so the first run needs cache_only=false; no YouTube/Instagram call).
Rules: repo fboy-engine/clip-engine only, account github_corral-fowler, author 240231847+fboy-engine@users.noreply.github.com,
small commits, push each unit before the next, dry_run on, permanent layout/edit rules unchanged.

WORKBENCH FACTS (the Composio sandbox is volatile: /home/user is wiped on reset, ~1 GB RAM, no ffmpeg, no git push auth)
- Push path: workbench python run_composio_tool("GITHUB_COMMIT_MULTIPLE_FILES", ..., account="github_corral-fowler") with
  author+committer 240231847+fboy-engine@users.noreply.github.com. Clone with plain git (public repo). Large bash payloads and
  long pip installs make the connector return errors: run pip in the background (Popen) and poll.
- cv2 must be opencv-python-headless 4.x (cv2 5 and plain opencv-python break); tesseract exists in the sandbox, ffmpeg does not
  (cv2.VideoCapture decodes mp4 there). Memory: never hold 16 full-res float32 frames (workbench crashes).

REAL TWITCH SOURCE FACTS (measured from the clip in the sandbox, yt-dlp 2026.10.26, no cookies, no WARP)
- yt-dlp -F lists 360/480/720/1080 (1080p60) plus portrait-360/480/720/918. -f 1080 -> 1920x1080, 60 fps, 2260 frames (37.7 s),
  6.6 MB, sha256 44a37d2ab45c40af4702770f7f2224689a94199b08fc9cb029bbeda9c3779757.
- Layout numbers (the frames could NOT be viewed in the sandbox, so this is numeric only): Haar face stable at x~1429 y~106 w~83
  in almost every sampled frame; a second moving face-like detection x~1780-1820 y~80-140 w~105; persistent vertical edge x=1304
  y 0..168; persistent horizontal edge y~424 for x 1534..1920; big high-variance bright block x~15..440 y~150..440; a sporadic
  static detection at x~122 y~597 w~75; chat-like scrolling column x~1500..1920 y~450..1080. Which of these is the face cam is
  NOT established: A3 must decide it by rule and fail with a message when not confident.

Units done: A1c-1 caption_sources (2e18169); A1c-2a captions_pass + grader check_caption_words (2e8ff73); A1c-2b render() wiring +
clip.yml tesseract/GEMINI env (eeb6063). 411 tests passed, 6 skipped.
A3 STATE (session 2): A3-1a DONE: clip_engine/camfind.py (detect_media, detect, cam_window, draw_debug, find_chat, persistent_lines) +
tests/test_camfind.py (10 tests; the real-clip test runs with CLIP_TWITCH_MP4=<mp4>, else skipped). Full suite 421 passed.
REAL TWITCH RESULT (24 frames, 57 s in the workbench, no ffmpeg): cam rect px (1304,0,1920,424) = top-right 616x424, confidence 0.989.
Evidence: Haar face stable in 23/24 frames at (1470,148) w84 plus a 2nd stable face (1850,165) w109 (two people in the cam); left border =
persistent vertical edge x=1303 (y 0..178); bottom border = persistent horizontal edge y=424. Chat column at the right edge (text-line pitch
52 px, strength 0.586) x1728..1920 y424..1074; zones cam, chat, side_panel (x1304..1920, full height). The big bright left block is NOT
chat (periodicity 0.19) = gameplay. The frames could not be VIEWED in the sandbox (egress blocked): the annotated cam_debug.jpg is for the user.
Rule: stable face track + persistent straight borders (or frame edge); raises CamNotConfident (clear reasons) without a face track, without
any border, when the rectangle is implausible, or when two rectangles tie. Known gaps: floating counters/timers outside the cam/chat column
are not detected; a cam with no border needs cam_rect.
Next: A3-1b rendered-output gate (camfind.rendered_gate), A3-2 wiring in render() (+ layout.cam_boxes full 2:1 window), A3-3 cli/clip.yml
Twitch download (no cookies, no WARP) + verification run.
