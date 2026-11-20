# STATUS

_Last updated 2026-11-21. No finished clip has been produced yet._

## Verified on real services
- Gemini: one real call passed (run 44805153126). `gemini-3.6-flash` answered in 6.8 s; 3.8 and 3.7 stalled 20 s each. Chain + 75 s deadline in `gemini.py`. See NOTES.md.
- WARP: egress check passes (runner datacenter IP -> Cloudflare WARP, `warp=on loc=US`).
- Download: anonymous (cookies off) yt-dlp through WARP, 60 s window of a live stream (Kai Cenat, LHoHInbPkk0), run 44808358316: h264 1920x1080 60 fps + AAC, 60.0 s, 12.7 MB, stream copy (no re-encode). Live DVR cut works with the same path.

## Works (unit-tested, synthetic data only)
- `gemini.py`, `rank_local.py` (payoff gate, 25-45 s), `cropplan.py` (9:16, punch-in cap 8% / hard 12%), `captions.py` (word-timed ASS, max 1 highlight), `config.py` (CRF 17, -14 LUFS, SFX/music off, dry_run on), `download.py` (redaction, client fallback, stream copy).

## Not built yet
- Transcribe (faster-whisper large-v3 + Silero VAD, top windows only), Gemini scoring/watching gates, visual and motion gates, cam_layout, caption_lane, select_clean, hook_title, hook_gemini, render_clip, grade_clip, grade_checks, asr_fix, auto-find window (`end_sec=0` is still rejected by `cli fetch`).

## Not verified
- Download from an ENDED public stream (the only successful target so far was live).
- Anything past download on a real stream.
- `YT_COOKIES` secret is stale; cookies are off by default and not needed so far.

## Known TODO
- `GEMINI_MODEL` should lead the chain instead of replacing it.
- Hosted-runner terms risk accepted by the user (see NOTES.md).
