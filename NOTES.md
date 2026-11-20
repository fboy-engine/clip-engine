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

## First download attempt (run 44805460806, 2026-11-20)
- WARP works: direct egress 135.119.111.254 (runner datacenter IP), via proxy a Cloudflare WARP IPv6 address, `warp=on loc=US`. Egress check passed.
- Target `KY4DWumZffg` (YouTube's official 24 h GTA VI launch stream, ended 2026-11-20 00:00 ET) now returns "Private video". Not downloadable by anyone not signed in as an authorised account, so it is the wrong test target; need a public ended stream URL.
- The `YT_COOKIES` secret is stale: yt-dlp warns the account cookies are "no longer valid ... rotated" (22 lines, 21 youtube/google). Using cookies from a logged-in personal account on datacenter IPs also risks that account, so the clip workflow now has a `cookies` input, default off (anonymous via WARP). Re-export cookies only if anonymous is blocked: from a private window, export, close the window without browsing further.
