# STATUS

_Last updated 2026-11-21 (find run 44824284792)._ No finished clip has been produced yet._

## CI
- unit-tests: green on main at 655c47b (58 tests). 7ad9bb6 and af98c0e were red: CI had no numpy; fixed in eb85dab by `pip install numpy`. Confirm green on GitHub after each push.

## Verified on real services
- Gemini: real call passed (run 44805153126). Model chain, 75 s deadline, MAX_CALLS=4. GEMINI_MODEL leads the chain. See NOTES.md.
- WARP: egress check passes on the hosted runner (warp=on loc=US).
- Download: anonymous yt-dlp through WARP, stream copy, 1080p60 h264 + AAC. Verified on a LIVE stream (60 s window, run 44808358316) and again in run 44816094672 (245 s, 85.2 MB). Failed once with "Sign in to confirm you're not a bot" (per-IP, transient) and once after 4 s on the Kai Cenat live (cause not yet read).
- Static ffmpeg from GitHub releases is cached; install step ~6 s.

- VOD download (ended stream LHoHInbPkk0, window 1200-1500 s, run 44821362284): anonymous via WARP, 1080p60 h264, 305 s, 211.8 MB, 17 s download. WARP now pinned (wgcf 2.3.1, wireproxy 1.1.3), no releases API.
- ASR on 4 vCPU runner, int8, beam 1, Silero VAD (0.5 s per 300 s; speech 90 s = 30% of the window). tiny: 4.05 s for 300 s audio (RTF 0.013), peak 531 MB, model 78 MB. small: 13.75 s (RTF 0.046), peak 1.1 GB, model 486 MB. large-v3: 45.9 s for the first 120 s (76 s speech; RTF 0.38), peak 5.2 GB, model 3.09 GB (download 24 s, load 4.6 s). Whole job 7 min incl. installs; models cached after the run.

## Built, unit-tested on synthetic data only
- gemini.py, rank_local.py (payoff gate, 25-45 s), cropplan.py (9:16, punch-in cap 8% / hard 12%), captions.py (word-timed ASS, max 1 highlight), config.py (CRF 17, -14 LUFS, SFX/music off, dry_run on), download.py.
- transcribe.py + `cli transcribe`: faster-whisper int8 + Silero VAD, ffmpeg decode (PyAV removed). Measured on a real run 44821362284 (see Verified).

## Not built yet
- Visual and motion gates (beyond the candidate-finding motion signal), cam_layout, caption_lane, select_clean, hook_title, hook_gemini, render_clip, grade_clip, grade_checks, asr_fix, auto-find window (end_sec=0 is rejected by `cli fetch`).

## Built and unit-tested (synthetic) this session
- analyze.py: per-second audio energy, VAD speech share, screen motion (ffmpeg 64x36 gray @4 fps), silence/speech-gap cut points, heat peaks -> 25-45 s windows. Not speech-only: payoff gate has a voice path and an action path (motion burst + audio lift, no speech needed).
- find.py / findrun.py / `cli find`: top windows -> tiny/small ASR on those windows only -> local gate -> Gemini text score -> Gemini watching gate (6 frames) on the top 3 -> report + out/candidates.json. A missing Gemini stage is reported, never counted as zero.
- download.py: bot check = one 30 s backoff + ONE retry, then BotCheck (stop and report). Cookie step fails clearly on an empty or stale YT_COOKIES.
- clip.yml inputs: find, find_model, find_top, cache_only (fail instead of downloading on a cache miss), gemini_model.

## Verified on a real run (44824284792, cached window 1200-1500, cache hit true, no download)
- audio decode 0.5 s, VAD 0.7 s (speech 90 s of 300), motion 32.3 s, small ASR on 8 windows 25.2 s, 8 candidate windows.
- Only 2 of 8 windows passed the payoff gate. Gemini scoring got HTTP 503 (gemini-3.8-flash high demand), so the Gemini score and the watching gate have NOT run on real data yet.

## Not verified
- Gemini scoring and watching gate on real data (503 in run 44824284792). Anything past candidate finding on a real stream.
- YT_COOKIES is stale; cookies are off by default and not needed so far.

## Workflow
- clip.yml: restore source cache first; a cache hit skips WARP, cookies, stream info and download. A fresh download is saved to the cache right after it succeeds (not at job end). transcribe_models runs one process per model.

## Next
- Re-run find once with gemini_model pinned to a model that answered before, then report the top 3 candidates before rendering anything.
- Then, in small commits with tests green: visual/motion gates, cam_layout, caption_lane, select_clean, hooks, render_clip, grade_clip, grade_checks, asr_fix; one reviewed clip.
