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

## RUN STATE
- Last run: 44924213343 on 8a5e730 (user reviewed; items above). No run started this session yet.
