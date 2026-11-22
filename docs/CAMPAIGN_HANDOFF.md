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
- B3b (in progress): workflow modes secrets/probe/init/tick; Campaign-tab cell "Open Reach submit page" (campaign.json `reach_url` = https://reachclipping.com/Sisters, one config value; it only opens the page, we never submit anything); sheet created through the Composio Sheets connector if COMPOSIO_API_KEY is not set yet; sheet id goes into campaign.json sheet.id.
- State at B3b start: the Google Sheet does not exist yet (account johndoe.gitz@gmail.com). IG secrets are set. COMPOSIO_API_KEY unknown until mode `secrets` runs.
