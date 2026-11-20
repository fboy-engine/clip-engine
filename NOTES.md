# NOTES: errors fixed and why

## Gemini 404
Cause: hardcoded `gemini-2.5-flash`. Google's deprecation page lists it with a shutdown date (a snapshot shows 2026-10-16; other snapshots disagree) and recommends the 3.x flash line. Current names seen in docs: gemini-3.5-flash, 3.6-flash, 3.7-flash (routes to 3.8), gemini-3.8-flash. Snapshots conflict, so the code does not hardcode a name: `GEMINI_MODEL` env wins, otherwise ListModels picks the newest `gemini-N.N-flash`.

## Gemini ReadTimeout / ~100 s failure (root cause found 2026-11-21)
Diagnostic run 44803994650 (curl timings, same runner): GET ListModels = HTTP 200 in 0.11 s, connect 6 ms, so network, DNS, key and IPv4/IPv6 are all fine (`-4` behaves the same as default). POST generateContent returned a fast HTTP 503 UNAVAILABLE "high demand" in 2-3 s for `gemini-flash-latest` and `gemini-3.7-flash`, and `gemini-3.8-flash` stalled to the 15 s probe timeout. So the cause is Google-side capacity on the newest flash models, not our side. The old ~100 s was 3 models x 4 tries x backoff x 45 s timeouts.
Fix: 2 tries per model (backoff 1 s), per-call timeout 25 s, whole-call deadline 75 s, chain = newest 5 `flash` / `flash-lite` names from ListModels then `gemini-flash-latest`, `gemini-flash-lite-latest` as last resorts. `gemini-check` now stops at the first model that answers and prints the flash-ish model names. Prompt cap 6000 chars stays. After GeminiUnavailable callers use `rank_local`.
Verified with ONE real run (44805153126): gemini-3.8-flash TimeoutError 20 s, gemini-3.7-flash TimeoutError 20 s, gemini-3.6-flash OK in 6.8 s (reply 'OK'). TODO: let GEMINI_MODEL be a preferred first model with the chain behind it (today it replaces the chain), so the two slow models stop costing 40 s.

## GitHub Actions terms / limits
GitHub Terms for Additional Products and Features: on GitHub-hosted runners, activity unrelated to production, testing, deployment or publication of the repo's software project is not allowed; misuse can end in job termination, Actions restrictions or disabled repos. A video clipping pipeline run on hosted runners does not fit that wording. Decision by the user (2026-11-20): use hosted runners on this public repo anyway and accept the risk; workflow_dispatch only, no cron, short runs, transcribe only top windows, small commits, no retry storms. Code stays runner-agnostic so it can move to a self-hosted runner. Expect YouTube to block datacenter IPs for yt-dlp (hence the `warp` input).

## Commit identity
Commits use author/committer `240231847+fboy-engine@users.noreply.github.com`. Earlier commits (up to fdfd3b1) carry a personal email in the git history; not rewritten (no force-push).

## First download attempts (2026-11-20)
- Run 44805460806: target `KY4DWumZffg` (YouTube's official 24 h GTA VI stream) returned "Private video" after it ended. Wrong target.
- The `YT_COOKIES` secret is stale: yt-dlp warns the account cookies are "no longer valid ... rotated". Using a logged-in personal account's cookies on datacenter IPs also risks that account, so the clip workflow has a `cookies` input, default off (anonymous via WARP). Re-export only if anonymous gets blocked: private window, export, close it without browsing further.
- Run 44806136531 hung 9+ min in the apt/pip install step and was cancelled. Fix: step timeouts (install 6 min, WARP 4, info 4, download 15, job 30) and apt/pip retry+timeout flags.
- Run 44807660941 (Kai Cenat live stream LHoHInbPkk0, which was LIVE, not ended; 60 s window at 600-660 s of the DVR; WARP on, cookies off): SUCCESS. Egress check: direct 20.62.254.160 (runner datacenter IP) vs WARP IPv6 `2a09:bac1:...`, `warp=on loc=US`. yt-dlp 2026.10.26, node v24.22.3, ffmpeg 8.0.1. Clients used: default (tv samsung + visionos player API), format 312+234 = 1080p60 H.264 + AAC. Anonymous download through WARP works; no cookies, no player_client override and no extra JS-runtime fix were needed.
- BUG found in that run: `--force-keyframes-at-cuts` made yt-dlp re-encode the section with ffmpeg defaults to MPEG-2 video (200 kb/s q=31) + MP2 audio in an MPEG-TS stream named source.mp4. That is a lossy source before our CRF 17 edit even starts. Fix: dropped the flag (stream copy, cuts land on keyframes, file can be a few seconds longer than asked); `fetch` now refuses mpeg2video/mpeg1video output; added a test that the flag stays out.
- Live mode: no separate path needed. For a live stream the same `--download-sections` cut works from the DVR window; `--live` is accepted and `cli fetch` no longer refuses it. Positions are relative to the first available DVR segment, not wall-clock.
- ffmpeg prints a progress line every 0.5 s into the log (hundreds of lines). Harmless; trim later if logs get hard to read.

## unit-tests RED since 6534c10 (fixed 2026-11-21)
Failing: test_core.T.test_timeout_retries_then_gives_up (9 != 3). Cause: the mixed chain (model + `gemini-flash-latest` + `gemini-flash-lite-latest`) x retries=3 = 9 calls on a timeout; the test still expected 3. Fix: `MAX_CALLS = 4` total generateContent calls per `generate()` (param `max_calls`), checked before every call, inside the 75 s deadline; raises GeminiUnavailable("... call cap 4"). The test now asserts `gemini.MAX_CALLS`; new test `test_total_call_cap_stops_the_chain`. Effect: with default retries=2 a dead chain tries at most 2 models, so a 503 on the lead models can no longer cost ~9 calls. Full suite 27/27 locally before push.

## GEMINI_MODEL now LEADS the chain (2026-11-21)
Before: GEMINI_MODEL replaced the whole chain, so a stalled pinned model had no fallback. Now `generate()` builds `[GEMINI_MODEL] + ranked ListModels candidates + -latest aliases` (deduped), still bounded by MAX_CALLS=4 and the 75 s deadline. If ListModels itself fails but GEMINI_MODEL is set, the pinned model is still tried alone.

## Transcription slice (2026-11-21)
`clip_engine/transcribe.py` + `cli transcribe`: faster-whisper int8 on CPU, Silero VAD (bundled with faster-whisper) for speech stats and `vad_filter`, word timestamps in the {"w","s","e","p"} shape captions.py takes. Each run records decode/VAD/download/load/transcribe seconds, RTF, peak RSS, model size and cache hit in `out/metrics_<model>.json`. The clip workflow got `transcribe_models` (comma list, empty = skip) and `large_max_sec`; one process per model so peak RSS is per model; models cached with actions/cache in ~/.cache/clip-engine/models.
Bug caught before it skewed numbers: a HuggingFace cache stores each file as blob + snapshot symlink, so naive directory sizing doubled the model size. `dir_size_mb` now counts each real file once (test added).

## Install step hung silently again (run 44812916359, 2026-11-21)
First real transcription run died at "Install tools" with only "timed out after 6 minutes" and no output (apt used `-qq`, so I cannot tell which command stalled; same family as run 44806136531). Nothing after it ran: no download, no transcription. Fix (diagnosis-first, no retry loop): apt forced to IPv4, `timeout` on apt update (120 s), apt install (240 s) and pip (150 s), timestamped phase echoes, step limit 8 min. If it stalls again the log will name the exact command.



## YouTube bot check on anonymous WARP (run 44814362584, 2026-11-21)
Install + WARP passed (run 2), then `cli info` failed: "Sign in to confirm you're not a bot" (default player client, WARP egress, no cookies). The earlier anonymous success (run 44807660941) used the same path, so this looks like a per-IP/per-time block, not a code bug. `info` is metadata only and tries ONE client, while `fetch` tries up to 3 (default, tv, mweb), so the info step is now continue-on-error and the download decides. If the download also gets the bot check, the options are: re-export YT_COOKIES (risks that account on datacenter IPs), or a different stream/IP; that is the user's call.
\n