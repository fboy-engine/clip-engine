# STATUS

_Last updated 2026-11-21. Nothing here is a passing real-clip result; no clip has been produced yet._

## Works (unit-tested locally, 12/12, synthetic data only)
- `gemini.py`: runtime model discovery (ListModels), retries + backoff, prompt capped at 6000 chars, raises `GeminiUnavailable` so callers use local scoring.
- `rank_local.py`: payoff gate (speech/reaction AND energy peak, 25-45 s).
- `cropplan.py`: smoothed 9:16 crop with deadband; punch-in capped 8% (hard 12%).
- `captions.py`: word-timed ASS, max 4 words/line, at most one highlighted word.
- `config.py`: CRF 17, -14 LUFS, SFX/music off, dry_run default true.

## Not built yet
- Download (yt-dlp), transcribe (faster-whisper large-v3 + Silero VAD), cam_layout, caption_lane, render_clip, grade_clip, hook_gemini, visual/motion gates, clip workflow.

## Not verified
- Gemini against the live API (no key used yet; model name is discovered at runtime, not confirmed on a real call).
- Anything on a real stream (action-heavy GTA 6 stream has not passed all gates; unchanged).

## Blocker
- GitHub-hosted runners: Additional Product Terms limit them to activity tied to producing/testing/deploying/publishing the repo's software. Video clipping is outside that. Heavy runs are on hold until a runner decision (see NOTES.md).
