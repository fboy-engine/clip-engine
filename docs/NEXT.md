# NEXT.md - READ THIS FIRST, EDIT IT AS YOU WORK

Repo: fboy-engine/clip-engine, branch main. Last updated: 2026-11-25 (by the planning Claude).
Every Claude: read this whole file, do the next undone item, tick it, commit after EVERY change (small commits, CI must stay green), and keep this file true. Trust `git log` over HANDOFF.md (stale). Other docs: docs/CAMPAIGN_HANDOFF.md (Part B).

## The user
- Android phone only. No PC, no paid hosting. Is in a hurry, wants it working TODAY. Usage limits are tight: work in small steps, commit often, never leave work uncommitted.
- Do not ask the user whether to continue. Decide, do, report. Ask only for things only they can do (accounts, tokens, link submissions).

## What the product is
From a stream link, find the best moment and build a 25-45 s 9:16 clip (1080x1920, 60 fps, x264 CRF 17), word-timed captions, hook within the first second.

## PERMANENT RULES (never break)
Edit: no generic AI-template look; each clip different; no stock SFX or music; original audio about -14 LUFS, true peak <= -1.5; cold-open hook within 1 s; one word-timed caption style, at most one highlighted word; natural cuts; punch-in <= 8%; no code boxes, emoji pop-ups, glitch or spin transitions; frame-accurate trim.
Layout: streamer cam detected, enlarged, fixed in the top 28% (1080x540), never shrinks. Bottom 72% (1080x1380) is gameplay, not stretched, main character tracked. Cam, chat, viewer counters, timer never appear in the gameplay panel. No cam-less layouts. No confident cam = skip the clip, never guess.
Campaign (Reach, "$5,000 Lauren Hamden GTA 6 Clipping Campaign"): Instagram Reels only, one account. Caption = exactly #LaurenHamden and @LaurenHamden + one short English line + at most 5 more tags. No logos/watermarks, no AI video. The USER submits each link to Reach by hand within 30 min of posting. Next APPROVE is 150 min after previous POSTED, only if next clip is READY.
**NO REAL UPLOAD until the user says "test upload now".** Dry-run only before that.
After the first approved clip: remind the user to pin the campaign to a tagged engine version (e.g. lauren-v1).

## STATE (as of the last check)
Part A = clip engine. Part B = campaign loop + Google Sheet + Apps Script panel.
- CI (unit-tests) green on all recent commits. Latest engine/campaign commit at time of writing: 811e220.
- **No clip has passed every gate yet.** This is the main blocker.
- Part B quota crashes are fixed (back-off, wait-and-retry, exit 0). Watchdog run 45546899576 succeeded. Loop run 45546917364 was still running (started 04:27Z). Loop marks clips that fail gates as SKIPPED, which can use up the source list: see B1.

### Best clip so far: Golden
- Run 45518671433: rendered, captions 96%, but GRADE FAIL: hook missing, subject-in-panel 77.4% (needs 95%).
- Run 45524414501: captions 90% FAIL (3.98 s gaps), hook found.
- Two Golden runs differ (trim/window, 83 vs 86 words, hook empty vs found). UNEXPLAINED nondeterminism. Find out why.
- Hook text for Golden contains a word that could get the Reel flagged: pick a different hook line or word-safe wording.
- Render-time tracking gate says 85% but post-render grade says 95% and measured 77.4%: the two disagree. Make them measure the same thing on the same frames, then fix the tracker or crop until real >= 95%.

## TO DO (in this order)
### Part A (engine)
- [ ] A1. Golden: explain the run-to-run difference (make trim/window deterministic), fix hook-empty, fix subject-in-panel to >= 95% on the post-render grade. Get ONE clip to pass ALL gates end to end. This is the goal.
- [ ] A2. Reconcile the 85% render gate with the 95% grade check (same sampling, same box rule).
- [ ] A3. Captions: Muddy (run 45390498494) needs real recovery of "nice and slow" (user is certain it is said) and maybe "looks like" (user unsure) to reach ~19.0 s of 21.1 s coverage. Optimistic (run 45524414788) captions 77%: tracking passes 96.1%. Do not fake words: only add a word when both audio passes hear it (p >= 0.5).
- [ ] A4. Muddy: second stacked cam. Slick, Peaceful (no cam -> skipped correctly). Sharpness gate (C). Stream rule: only GTA VI chapters of https://m.twitch.tv/videos/2934045907 plus a confident cam.
- [ ] A5. Gemini returns HTTP 403 (permission/key/API-not-enabled/region, NOT a rate limit). Engine treats it as final and unavailable, so captions rely on Whisper + rescue only. Low priority: find the cause (check the Gemini key secret, API enabled, region) only if it helps close caption gaps. It must never block or crash a run.
- [ ] A6. Ask the user what the A4 reference clip is (open question) - only if truly needed.
### Part B (campaign)
- [ ] B1. Add a PAUSE switch (sheet cell or campaign.json flag) so the loop does not render/skip sources until Part A passes a clip. Do not let gate failures burn the source list (failed clips should be retried or parked, not silently consumed).
- [ ] B2. End-to-end dry run of the panel: APPROVE -> dry-run upload -> permalink and "submit to Reach before HH:MM" -> 150-min wait -> next clip. Check the Apps Script panel (docs/apps_script/lauren/) is redeployed with the latest code.
- [ ] B3. Decide how the video reaches Instagram. The Graph API container needs a PUBLIC video URL (a GitHub Actions artifact is NOT public). Options: a public GitHub Release asset / raw URL on this public repo, or resumable upload. Implement and dry-run it.
- [ ] B4. Finish the "Ready for first real post" checklist in docs/CAMPAIGN_HANDOFF.md.
- [ ] B5. Only after the user says "test upload now": do one real upload, report permalink, remind about tagging lauren-v1.
### Later / user decisions (do not start)
- Rewrite git history to remove a personal email from old commits (717ad3c, af9a12f, ae1f5bd, 31fecf3, b82c3a3) - needs user OK, force-push.
- Later phases: 8 Instagram accounts, 2 YouTube channels, contentrewards sync OFF.

## THE QUOTA PROBLEM (plain words)
- The loop reads/writes a Google Sheet through Composio. Google limits "read requests per minute" per Google project. Composio uses ONE shared Google project for all its customers, so the limit is shared with strangers. Our own load is tiny (1-2 reads/min) but the shared limit still gets hit and the loop got a 429 error and crashed.
- FIXED in software (commits 6b917c1, 2fb3c00, 474f65c, f5c1333, ae8a602, 811e220): reads are batched, a quota error now means wait with exponential back-off and jitter, then retry, never crash, exit 0.
- Effect on timing: a quota wait is seconds to a couple of minutes against a 150-minute gap, so posting time is barely affected.
- Permanent fix (optional): give the engine its OWN Google service account so the quota is ours alone. That is Google Cloud console work (create project, enable Sheets API, create service account + JSON key, share the sheet with the service account email, store the key as a GitHub secret). Heavy on a phone: do it only if the back-off proves not enough.
- Another API? Yes, the real fix is simply not sharing the quota: own Google service account, OR move the queue off Sheets (a JSON/CSV file in the repo or a GitHub Issue/Project as the queue, with the Sheet only as a display). Not an Instagram issue: Instagram is a separate API.

## INSTAGRAM (untested)
Graph API publishing needs: Business/Creator IG account linked to a Facebook Page, instagram_content_publish permission, container must reach FINISHED before publish, public video URL. User says their IG token does not expire (Page/system-user tokens do not; plain long-lived user tokens last 60 days).

## GITHUB SCHEDULER CAVEATS
Cron can be delayed or skipped; scheduled workflows get disabled after 60 days of repo inactivity; jobs stop at 6 h (the loop hands off at 5h30).

## HOW TO WORK (tools)
- Repo access via Composio GitHub tools, account alias "yobf" (login fboy-engine). Commit with GITHUB_COMMIT_MULTIPLE_FILES as fboy-engine <240231847+fboy-engine@users.noreply.github.com>. Never use any personal email.
- Workflow "clip" (clip.yml) takes 10-35 min per run: dispatch, then check logs/artifacts (render.json, layout.json, grade.json, captions_check.json, tracking.json). Check logs yourself; do not ask the user to.
- Keep this file updated: tick items, add findings (with run ids and commit shas), and rewrite STATE at the end of every session.

## REPORT FORMAT (end of every session, also put in the reply)
1. What is left (unticked items). 2. What was fixed this session (commit shas). 3. Quota status (loop alive? any 429 waits?). 4. What the user must do, if anything (one line each).
