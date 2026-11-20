# STATUS

_Last updated 2026-11-21. No finished clip has been produced yet._

## CI
- unit-tests: green on main at 84a4acd. Confirm green on GitHub after each push.

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
- Gemini scoring/watching gates, visual and motion gates, cam_layout, caption_lane, select_clean, hook_title, hook_gemini, render_clip, grade_clip, grade_checks, asr_fix, auto-find window (end_sec=0 is rejected by `cli fetch`).

## Not verified
- Anything past download on a real stream.
- YT_COOKIES is stale; cookies are off by default and not needed so far.

## Workflow
- clip.yml: restore source cache first; a cache hit skips WARP, cookies, stream info and download. A fresh download is saved to the cache right after it succeeds (not at job end). transcribe_models runs one process per model.

## Next
- ASR design from numbers (see NOTES.md): VAD first, tiny/small on candidate windows, large-v3 only on the final 25-45 s clip. Next: wire ranking + Gemini gates on the cached window.
