# NEXT.md - READ THIS FIRST, EDIT IT AS YOU WORK

Repo: fboy-engine/clip-engine, branch main. Last updated: 2026-11-25 ~16:00 Karachi (11:00Z) by Claude session 3.
Every Claude: read this whole file, do the next undone item, tick it, commit after EVERY change (small commits, CI must stay green), and keep this file true. Trust `git log` over HANDOFF.md (stale). Other docs: docs/CAMPAIGN_HANDOFF.md (Part B).

## The user
- Android phone only. No PC, no paid hosting. In a hurry, wants it working TODAY. Usage limits are tight: small steps, commit often, never leave work uncommitted.
- Do not ask whether to continue. Decide, do, report. Ask only for things only they can do (accounts, tokens, link submissions).
- Plain words please. The user is tired and reads on a phone.

## What the product is
From a stream link, find the best moment and build a 25-45 s 9:16 clip (1080x1920, 60 fps, x264 CRF 17), word-timed captions, hook within the first second.

## PERMANENT RULES (never break)
Edit: no generic AI-template look; each clip different; no stock SFX or music; original audio about -14 LUFS, true peak <= -1.5; cold-open hook within 1 s; one word-timed caption style, at most one highlighted word; natural cuts; punch-in <= 8%; no code boxes, emoji pop-ups, glitch or spin transitions; frame-accurate trim.
Layout: streamer cam detected, enlarged, fixed in the top 28% (1080x540), never shrinks. Bottom 72% (1080x1380) is gameplay, not stretched, main character tracked. Cam, chat, viewer counters, timer never appear in the gameplay panel. No cam-less layouts. No confident cam = skip the clip, never guess.
Campaign (Reach, "$5,000 Lauren Hamden GTA 6 Clipping Campaign"): Instagram Reels only, one account. Caption = exactly #LaurenHamden and @LaurenHamden + one short English line + at most 5 more tags. No logos/watermarks, no AI video. The USER submits each link to Reach by hand within 30 min of posting. Next APPROVE is 150 min after previous POSTED, only if next clip is READY.
**NO REAL UPLOAD until the user says "test upload now".** Dry-run only before that.
After the first approved clip: remind the user to pin the campaign to a tagged engine version (e.g. lauren-v1).

## STATE (rewritten 2026-11-25 ~15:10 Karachi / 10:10Z by Claude session 2)
Part A = clip engine. Part B = campaign loop + Google Sheet + Apps Script panel.
- CI (unit-tests) green on every push of this session (176d077, 99c8cc5, 5bb6122, ae6a96e, cddd9e3, 878f114).
- **Still no clip has passed every gate.** The blockers are now known per clip (see FINDINGS). The campaign loop is PAUSED (campaign.json "pause": true, cddd9e3): it renders nothing and skips nothing until a clip passes. Set it to false to run.
- Loop run 45546917364 was healthy at 08:45Z (self-hands-off ~09:57Z, watchdog restarts it). Check the newest campaign-lauren-hamden run before relying on this line.
- Gemini: key works but Google answers 403 "free tier not available for this project, set up billing" on every model. USER ACTION needed (billing on `clip-enginex`). Engine runs without Gemini (local ASR, local hook).
- Real source (VOD https://m.twitch.tv/videos/2934045907, "GTA 6 LAUNCH!!!!", 3772 s) DOWNLOADS FINE, anonymous, no cookies, no WARP (7510e1e sent the VOD to the section download; the old "Requested format is not available" came from the clip download path, not from a sub-only VOD). Proof: run 45594570299 (find, 10:23Z, success): 1080p60 section 300-900 s, 470 MB, PASS download, 8 candidates ranked, Gemini 403 -> local score only. CI (unit-tests) green on 7510e1e.
- VOD find result (file s | stream s = 300 + file): id1 85-119 | 385-419 (36% speech, score 7.7); id3 112-147 | 412-447 (44%); id2 366-399 | 666-699 (46%); id6 343-378; id0 0-35 | 300-335 (66% speech, score 5.62, densest speech); id7 137-172; id5 306-343. Speech is 36-66% in every window: the 75% word-coverage backstop may fail the sparse ones. Chapter (GTA VI or not) is NOT yet verified: info shows no chapters.
- VOD render attempts (dry run, 7510e1e, section 300-900 s, cam auto-found every time at top-right, conf 0.92-0.93): file 85-121 (run 45595653086): cam OK, hook "why is he getting up you", 29 words in 28.4 s, but tracking gate 68.4% (needs 85%): FAIL. File 0-36 (run 45595655692): cam OK (conf 0.934), hook "I also have my own money", 54 words, no overlay in panel, render tracker 85.7%, panel check 89.9% inside on 98.6% detected of 70 frames (needs 95%), cuts 0/0.5/1/4.5/13.5/29.5/33.5 s: FAIL. Captions/loudness gates were not reached in either run. Not fixed by code yet; campaign stays PAUSED.
- More VOD renders (dry run): file 366-402 (run 45597571758): cam NOT confident (best candidate is the whole frame), cam-bench refused, skip is correct, no cam_rect guessed. File 112-149 (run 45597571934, stream 412-449 s) is the CLOSEST so far: cam OK (0.92), hook "I did a good job, no?", overlay OK, tracking 87.3% (gate 85%), panel check 97.4% inside (on 70.9% detected frames of 55). FAILS captions only: 79% of speech seconds covered (needs 90%) and 40% of the 27.5 s window (backstop 75%). 70 ASR words in the span, 51 caption words. Next step = A3 on this clip: read out/captions_check.json and captions_gaps.json from run 45597571934 and recover real missed words (p >= 0.5 in both passes). The 40%-of-window backstop will still fail unless the window is speech-dense (44% VAD speech); choose a denser window or ask the user about the rule.
- VOD render attempts, session 3 (10:52Z, 7510e1e/87e6068, dry run, no upload, loop still PAUSED): file 112-147 = run 45599082777 (url m.twitch.tv), file 366-399 = run 45599083307 (url www.twitch.tv, different URL only so the per-URL concurrency group does not drop one). Result not read yet: check both before dispatching more. Remaining untried windows: id6 343-378, id7 137-172, id5 306-343. Note: Muddy re-run 45591549780 result (panel check 93.4%, FAIL) IS recorded below.
- VOD results (session 3): file 366-399 = run 45599083307: cam OK, tracking 98.0%, panel check 95.3% PASS, overlay OK, hook OK, but captions FAIL: 15.5 s speech in a 25.2 s clip = 62% (needs 75%); speech is only 95% covered by words, i.e. the window is just too quiet (same sparse-speech problem as Optimistic). File 112-147 = run 45599082777: cam OK, hook 'did a good job no oh', panel check 94.9% inside but only 69.6% of 56 frames detected: FAIL. Next dispatched (11:0xZ): 343-378, 137-172 (www url), 306-343 (twitch.tv url), run ids [45600474364, 45600473929, 45600473684].
- Test-clip scan with the new code (all dry-run, no upload):
  - Golden (45587614373): hook OK ("speech_local"), panel check 77.4% (needs 95%): refused early. Unframeable, see FINDINGS.
  - Optimistic (45587614506): panel check 100%, tracking 97.7%, hook "The car's about to blow up". FAILS captions only: 74% of 14.9 s speech (need 90%) and words cover 29% of the 43.9 s window (backstop needs 75%). Sparse speech, not fixable by ASR.
  - Slick (45589979889): no confident cam, skip is correct. Cogent (45589991378): silent in the window, skip. Attractive (45589995827): last word at 22.1 s, no 27-29 s cut point; tracking was 50.8%. Muddy first try (45589974573): no word-end cut point 27-29 s (window too tight). Muddy re-run 0-36 s (45591549780, 878f114) FAILED at the panel check: 93.4% inside on 95.3% detected of 64 frames (needs 95%), cuts at 4.5/7/20/24.5 s; render tracker said 93.8%; ASR 64 words, trim 32.2 s; cam found top-right (1304,0,1920,220) conf 0.885. Close, but not passing. Tall re-run 0-31 s (45591555742) FAILED: 8 words in 33 s (near silent), envelope 0-31 s shorter than 25 s. Skip Tall.
- Not done: A3 (captions recovery), A4, A5, B2, B4, B5.

## FINDINGS 2026-11-25 (Claude session 2)
- **A0 Gemini**: key + secret name OK (GEMINI_API_KEY in code and workflows). ListModels works, chain starts gemini-3.8-flash. EVERY model answers HTTP 403 "free tier is not available for this project, set up billing" (gemini-check run 45579380310). Code cannot fix it. USER: enable billing on Google Cloud project clip-enginex (or make a key in a project/account where the free tier exists). Engine does not crash: it falls back to local ASR + local hook. Old Golden runs also had this 403, so Gemini is NOT the cause of the run-to-run difference.
- **A1 hook-empty**: local ASR gives lowercase text with no punctuation and no pauses, so no sentence or clause fit 6 words. Fixed 176d077: deterministic local phrase (hooks.local_phrase), flaggable words filtered (hooks.UNSAFE, run 45524414501 had "gay" in its hook). Tests: tests/test_hook_local.py.
- **A1 nondeterminism (unproven)**: runs 45518671433 vs 45524414501 differ in transcript (83 vs 86 words, capitals, "shout" vs "Shower") on the same clip. Same model/beam; asked end differed (40 vs 37) but both clamp to the 37.6 s file. Likely int8 numerics differing by runner CPU. 99c8cc5 logs an "asr fingerprint" (cpu, threads, span, transcript sha) in every render. If two runs on the same input show different sha with different cpu, pin float32.
- **A1/A2 subject-in-panel**: render gate (91.2%) and grade (77.4%) measured different things. Render tracker detects on a 960 px whole frame; grade detects on the 540 px panel. At every grade "cut" time (2, 2.5, 5, 6.5, 13, 14.5, 15, 20, 20.5, 23, 23.5, 26.5 s) the tracker had a SECOND, bigger person box (x 513-1304) ending at the cam/side-panel edge (crop can reach at most x=1299). Golden's character fills 75-90% of the crop and touches the right limit; this cannot be framed by moving the crop. Golden is a poor source for the 95% rule. 5bb6122: panel_check runs the grade's measurement before the cam work and refuses the clip with the grade's numbers.
- **A2 proven** (run 45587614373): the new panel check printed 77.4% inside on 93.0% detected, cut at the SAME 12 times the grade reported. Render gate and grade now measure the same frames; the 85% tracker gate stays as an early filter only.
- **Nondeterminism**: fingerprints now logged. Golden transcripts differ by runner CPU (AMD EPYC 7763 gives the "Shower" transcript, sha 1ff44d5c4140; the 00:31Z run with "shout" was a different CPU). Optimistic cut also changed (25.5 s earlier, 43.9 s now) because the ASR text changed. Fix still open: pin float32 or fixed numerics so the same input gives the same transcript on every runner. Not done.
- **Captions backstop**: speech_spans.MIN_RAW_COVERAGE 0.75 = words must cover 75% of the whole clip window. Clips with sparse speech (Optimistic) can never pass; choose speech-dense sources (the streamer talking most of the time). Rule left untouched.
- **B3 already built**: campaign_lib/instagram.py uses the RESUMABLE upload (no public video URL needed), dry-run plan + "ONE:<n>" sheet switch. Untested live (as intended).

## TO DO (in this order)
### Part A (engine)
- [x] A0 (DONE, needs USER action, see finding). Gemini: confirm the new key works inside the engine. Check which Gemini model name the engine calls (AI Studio lists models like gemini-4-hydrogen; an old name may fail) and that the secret name the workflow reads matches the secret the user set. Run ONE clip and confirm the log no longer says 403. Must never block or crash a run.
- [~] A1 (hook fixed 176d077, nondeterminism diagnosed, panel check 5bb6122; no clip passes yet). Golden: explain the run-to-run difference (make trim/window deterministic), fix hook-empty, fix subject-in-panel to >= 95% on the post-render grade. Get ONE clip to pass ALL gates end to end. THIS IS THE GOAL.
- [x] A2 (code done 5bb6122, proof pending a run). Reconcile the 85% render gate with the 95% grade check (same sampling, same box rule).
- [ ] A3. Captions: Muddy (run 45390498494) real recovery of "nice and slow" and maybe "looks like" (user unsure) to reach ~19.0 s of 21.1 s. Optimistic captions 77%. Never fake words: add a word only when both audio passes hear it (p >= 0.5).
- [ ] A4. Muddy second stacked cam. Slick. Sharpness gate (C). Stream rule: only GTA VI chapters of https://m.twitch.tv/videos/2934045907 plus a confident cam.
- [ ] A5. Ask the user what the A4 reference clip is (only if truly needed).
### Part B (campaign)
- [x] B1 (cddd9e3). Add a PAUSE switch (sheet cell or campaign.json flag) so the loop does not render or skip sources until Part A passes a clip. Failed clips must be parked as NEEDS_FIX / retried, never silently consumed as SKIPPED.
- [ ] B2. End-to-end dry run of the panel: APPROVE -> dry-run upload -> permalink and "submit to Reach before HH:MM" -> 150-min wait -> next clip. Confirm the Apps Script panel (docs/apps_script/lauren/) is deployed with the latest code. The panel runs on Google's side under the user's account; it is NOT hit by the shared Composio quota. Only the GitHub loop's Sheet access via Composio is.
- [x] B3 (already in instagram.py, resumable upload, no public URL). Decide and build how the video reaches Instagram. Graph API container needs a PUBLIC video URL (a GitHub Actions artifact is NOT public). Options: a public GitHub Release asset on this public repo, or resumable upload. Dry-run it.
- [ ] B4. Finish the "Ready for first real post" checklist in docs/CAMPAIGN_HANDOFF.md.
- [ ] B5. Only after the user says "test upload now": one real upload, report permalink, remind about tagging lauren-v1.
### Later / user decisions (do not start)
- Rewrite git history to remove a personal email from old commits (717ad3c, af9a12f, ae1f5bd, 31fecf3, b82c3a3): needs user OK, force-push.
- Later phases: 8 Instagram accounts, 2 YouTube channels, contentrewards sync OFF.

## THE TARGET LOOP WORKFLOW (the "perfect" end state; build toward this, keep it true)
1. PICK: loop takes the next QUEUED source (GTA VI chapters of the Twitch VOD with a confident cam). If PAUSE is on, it only idles and keeps the lock heartbeat.
2. EDIT: state EDITING. Dispatch workflow `clip` for that source (one run per source URL, concurrency group in place). Gates: cam find, overlay, tracking, captions, opening/hook, trim, loudness, caption style/lane, cam block, visual/motion, MIN_LEN 25 s, post-render grade (subject in panel >= 95%).
3. RESULT: all gates pass -> READY (clip stored where the panel can show it, caption text built per campaign rules). Any gate fails -> NEEDS_FIX with the reason in the Sheet; retry with a different window up to 2 times; only then SKIPPED. Never post a failing clip, never guess a cam.
4. APPROVE: panel shows the READY clip (state AWAITING_APPROVAL). The user taps APPROVE on the phone. Rule: next APPROVE is only possible 150 min after the previous POSTED, and only if the next clip is READY. While waiting, the loop pre-renders the next clip.
5. POST: state POSTING. Upload to Instagram Reels via the Graph API (public URL -> create container -> wait until FINISHED -> publish). Dry-run mode until the user says "test upload now".
6. POSTED: Sheet shows the permalink and "Submit to Reach before HH:MM" (30 min). The USER submits the link to Reach by hand, then marks it. Then DONE and the 150-min clock runs from POSTED.
7. KEEP ALIVE: 60 s tick, Sheet lock with heartbeat, 5h30 handoff to a new run, cron watchdog restarts a dead loop, Sheets quota waits use back-off and never fail a tick (exit 0), GitHub cron can be late (accept it). Scheduled workflows get disabled after 60 days of repo inactivity.
8. VERSIONING: after the first approved clip, tag the engine as lauren-v1 and pin the campaign to it.

## DEFINITION OF DONE FOR TODAY
One clip passes every gate -> READY -> user APPROVE in the panel (dry run) -> permalink + "submit before HH:MM" shown correctly. After the user says "test upload now": one real post. Everything else is later.

## THE QUOTA PROBLEM (plain words)
- The loop reads/writes a Google Sheet through Composio. Google limits "read requests per minute" per Google project, and Composio uses ONE shared Google project for all its customers, so the limit is shared with strangers. Our load is tiny (1-2 reads/min) but the shared limit still got hit: the loop got a 429 and crashed.
- FIXED in software (6b917c1, 2fb3c00, 474f65c, f5c1333, ae8a602, 811e220): reads batched, quota error = wait with exponential back-off + jitter, retry, never crash, exit 0. A wait is seconds to a couple of minutes against a 150-minute gap, so post timing is barely affected.
- Optional permanent fix: give the engine its OWN Google service account (Cloud console: project, enable Sheets API, service account + JSON key, share the Sheet with its email, key in a GitHub secret). Heavy on a phone: do it only if the back-off proves not enough. Do NOT move the queue off Sheets (the panel depends on it) unless forced.
- Instagram is a separate API and is not affected.

## INSTAGRAM (untested)
Graph API publishing needs: Business/Creator IG account linked to a Facebook Page, instagram_content_publish permission, container must reach FINISHED before publish, public video URL. User says their IG token does not expire (Page/system-user tokens do not; plain long-lived user tokens last 60 days).

## HOW TO WORK (tools)
- Repo access via Composio GitHub tools, account alias "yobf" (login fboy-engine). Commit with GITHUB_COMMIT_MULTIPLE_FILES as fboy-engine <240231847+fboy-engine@users.noreply.github.com>. Never use any personal email. The repo is PUBLIC: never put keys or secrets in files or commits.
- Workflow "clip" (clip.yml) takes 10-35 min per run: dispatch, then check logs/artifacts (render.json, layout.json, grade.json, captions_check.json, tracking.json). Check logs yourself; do not ask the user to.
- Keep this file updated: tick items, add findings (run ids, commit shas), rewrite STATE at the end of every session.

## REPORT FORMAT (end of every session, also put in the reply)
1. What is left (unticked items). 2. What was fixed this session (commit shas). 3. Quota status (loop alive? any 429 waits?). 4. What the user must do, if anything (one line each).
