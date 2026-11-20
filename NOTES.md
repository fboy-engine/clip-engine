# NOTES: errors fixed and why

## Gemini 404
Cause: hardcoded `gemini-2.5-flash`. Google's deprecation page lists it with a shutdown date (a snapshot shows 2026-10-16; other snapshots disagree) and recommends the 3.x flash line. Current names seen in docs: gemini-3.5-flash, 3.6-flash, 3.7-flash (routes to 3.8), gemini-3.8-flash. Snapshots conflict, so the code does not hardcode a name: `GEMINI_MODEL` env wins, otherwise ListModels picks the newest `gemini-N.N-flash`.

## Gemini ReadTimeout
Prompts capped at 6000 chars, 45 s timeout, 4 tries with exponential backoff (429/5xx/timeouts), then `GeminiUnavailable` -> local rank_local scoring.

## GitHub Actions terms / limits
GitHub Terms for Additional Products and Features: on GitHub-hosted runners, activity unrelated to production, testing, deployment or publication of the repo's software project is not allowed; misuse can end in job termination, Actions restrictions or disabled repos. A video clipping pipeline run on hosted runners does not fit that wording. Plan: hosted runners only run `unit-tests.yml` (synthetic data). Fallback for real video: a self-hosted runner (the hosted-runner clause does not apply) on a free/cheap VPS or a PC, same workflow via `runs-on`. Also expect YouTube to block datacenter IPs for yt-dlp.
