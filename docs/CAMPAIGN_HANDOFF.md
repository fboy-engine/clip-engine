# CAMPAIGN HANDOFF (Lauren Hamden campaign, Part B)

Owner of this file: the campaign Claude. The render/caption/cam code (clip_engine/) belongs to the other Claude: do not edit it from here. Repo fboy-engine/clip-engine, ONLY Composio account github_corral-fowler, noreply identity, small commits, push after each unit.

## Rules that must never be weakened
- Instagram Reels only, one account, English captions/on-screen text. No logos or watermarks added by us. Report burned-in logos in sources.
- Caption: exactly the ASCII strings #<handle> and @<handle> + ONE short natural English line + at most 5 more decent hashtags. The handle is ONE value: campaign.json "handle" (currently LaurenHamden). The user confirmed her real Instagram username differs, but the Reach rules say to use #LaurenHamden and @LaurenHamden exactly, so that is what is built. Changing it later = change that one line (and the literal strings in tests/test_campaign_caption.py::test_reach_rule_both_exact_ascii_strings).
- No AI-generated video. Enhance chain = quality upgrade of the same video only. Face fidelity gate kept. Never weaken it.
- Normal Reel, no collab, no reposts, no botting. The user submits the link to Reach MANUALLY within 30 min of posting. Never auto-submit.
- NO Instagram call or upload of any kind until the user says "test upload now". Dry-run builds the container without publishing.
- Account bio rule (the bio must contain https://lozzahamden.click/): this is the USER's account setup, NOT the system's, and it is already done. The system does nothing about it.

## Secrets (names only, never values)
IG_ACCESS_TOKEN_LAUREN and IG_USER_ID_LAUREN: the user already added them. COMPOSIO_API_KEY: needed from B3 (runner -> Composio -> Google Sheets). GEMINI_API_KEY: existing. Verified only as "set" / "NOT SET" by workflow mode `secrets` (never value, prefix or length).

## Layout
- campaigns/lauren-hamden/campaign.json: name, handle, caption rules, timings (150 / 30 min), sheet id, secret names, 10 sources.
- campaign_lib/ (stdlib only, no Instagram code anywhere in it):
  - config.py, caption.py, states.py (B2).
  - composio.py (B3a): minimal Composio v3 REST client. Key from env COMPOSIO_API_KEY (never printed, scrubbed from errors). Discovers the ACTIVE googlesheets connected account itself. execute(slug, args) = POST /tools/execute/<slug>. One retry on 429/5xx/network.
  - sheet.py (B3a): layout + SheetIO. Campaign tab = labels in col A, values in col B (state, next, caption, approve, timer, opens, link, submit, add_link, add_confirm, updated; machine cells unlock_utc, log_rows). Queue tab = 13 columns (#, Source, Kind, State, Moment, Hook, Caption, Note, Posted at, Media id, Reel link, Submit before, Updated). Log tab = Time, Event, Detail. read() = GOOGLESHEETS_BATCH_GET, write() = GOOGLESHEETS_UPDATE_VALUES_BATCH (RAW, one range per named cell, so a user edit between read and write is not overwritten). init_payload() builds a fresh sheet (real sources or fake clips).
  - runner.py (B3a): tick() = read sheet, add link (only when all used), fake edit, states.plan, fake POST (clearly fake id + permalink FAKE<n>), write Campaign box. A real (non-fake) clip with an APPROVE tick is NOT posted: tick cleared, log says uploader not enabled.
  - cli.py (B3a): python -m campaign_lib.cli secrets|probe|init|tick (--sheet-id, --fake, --gap-min, --submit-min).
- .github/workflows/campaign-lauren-hamden.yml: own file, own concurrency group `campaign-lauren-hamden`. clip.yml is not touched. Another campaign = its own folder/workflow/group/cache keys; it must never change or reset this one.
- tests/test_campaign_*.py (run by unit-tests.yml with unittest discover).

## State machine (campaign_lib/states.py)
QUEUED -> EDITING -> READY -> AWAITING_APPROVAL -> POSTING -> POSTED -> DONE.
- AWAITING_APPROVAL only for the head clip, only when READY and 150 min after the previous POSTED time (first clip: no wait). A tick before that is cleared and never counts; a tick seen in the same pass as the unlock is also cleared (needs a fresh tick).
- POSTED only with media id AND permalink. DONE 30 min after POSTED (Reach submit window over).
- POSTING failure goes back to AWAITING_APPROVAL (tick again). If the user is offline everything waits; nothing uploads by itself.

## Design decisions
- Repo is PUBLIC: Actions minutes are free. Polling still done in short runs (no long idle waits), chain of runs each well under 6 h, all state in the sheet.
- Runner reaches the sheet through the Composio API with COMPOSIO_API_KEY (one paste on Android; a Google service account would need Google Cloud console steps).

## Build log
- B1 (5ed0838, e30344b): link check, run 45045375994: 10/10 links OK (clip 8 = 19 s "short source"; stream 3772 s, has Audio_Only).
- B2 (caaebab): campaign.json, state machine, caption builder + tag test.
- B3a (426bc11): composio.py, sheet.py, runner.py, cli.py. Fake clips only. NOTE: its commit message mentions workflow modes, but the workflow on main still only had `linkcheck` (the previous Claude hit its usage limit before pushing them). Modes secrets/probe/init/tick come in B3b.
- B3b (b49224e, aa1cbfc..3fb2272, 0f74bda, 819aab0, 156ce0f, 24b11ed): workflow modes secrets/probe/init/tick (job `cli`, shell bash = pipefail; before that a failing step was shown green behind `tee`); Reach cell C10 "Open Reach submit page" (campaign.json `reach_url`, HYPERLINK formula written USER_ENTERED by init; it only opens the page, we never submit); sheet created with the Composio Sheets connector: "Clip Engine - Lauren Hamden GTA6", id in campaign.json (sheet.id), tabs Campaign/Log/Queue, checkbox validation on B7 (APPROVE) and B13 (confirm add link), Log grid 2000 rows; composio.py scrubs key-like strings from errors.
- Run `secrets` (45053095820): IG_ACCESS_TOKEN_LAUREN, IG_USER_ID_LAUREN, COMPOSIO_API_KEY, GEMINI_API_KEY all "set".
- BLOCKER: run `probe` (45053387200) FAILS: Composio answers HTTP 401 "Invalid API key" for the stored COMPOSIO_API_KEY. The user must paste a valid project API key (Composio dashboard, API keys) into the repo secret COMPOSIO_API_KEY. Until then runner-on-Actions (probe/init/tick) cannot reach the sheet.
- Loop proof so far (NOT via Actions): runner code (campaign_lib.runner/sheet) run from the Claude side with the Claude Composio Sheets connector as transport, against the real sheet, fake clips, no Instagram: READY then "APPROVE is open now"; tick -> FAKE POSTED with FAKE permalink; "submit to Reach before <+30 min>"; "APPROVE opens in 2h 30m"; add link rejected while a clip is unused, accepted when all used (queued as vod). Tool call arguments of GOOGLESHEETS_BATCH_GET / UPDATE_VALUES_BATCH match the real schemas.
- Sheet currently left in demo state (2 fake clips, #1 awaiting APPROVE). Reset = workflow init with fake 0 (real 10 sources).
- NEXT: after a valid COMPOSIO_API_KEY: run probe, init (fake 2), tick (fake) via Actions and confirm green; then B4+ (real clips, uploader only after "test upload now").

## Control panel (B3d, Apps Script web app)
- docs/apps_script/lauren/: Code.gs, Index.html, appsscript.json, README.md (phone setup + update steps). Private page (executeAs USER_DEPLOYING, access MYSELF, scope: spreadsheets only, no external requests). The sheet stays the database and must stay unshared; the engine is unchanged.
- Writes only B7 (APPROVE), B12 (add link), B13 (confirm). APPROVE is re-checked under LockService (same clip still AWAITING_APPROVAL, time passed, not already ticked). ADD LINK only when all clips are used.
- ONE cell map in Code.gs (CELL_MAP_BEGIN..END) mirrors campaign_lib/sheet.py; tests/test_apps_script_panel.py fails on any difference (also sheet id, tz, reach url, 30 min window vs campaign.json). tests/apps_script_panel_test.js = state-to-button logic, countdown format, link validation, server functions on a fake sheet.
- Known gap: sheet.TWITCH_URL accepts Twitch links only; the panel also accepts youtube.com (as asked), the engine then logs ADD_LINK_REJECTED until the regex is widened.
- B3c (ffd2321, 7f537eb): Composio client retries a 401 once with the x-consumer-api-key header, strips whitespace/quotes from the key, reports the primary header's answer. Probes 45055883510, 45056155790, 45056277151 (with fallback), 45056467090 (stripped key): all HTTP 401 "Invalid API key" on GET /connected_accounts, so init/tick via Actions stay blocked until a valid COMPOSIO_API_KEY is in the secret. Sheet still in demo state (2 fake clips).
