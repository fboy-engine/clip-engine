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
- campaign_lib/: config.py, caption.py, states.py (+ sheet/runner from B3). Stdlib only.
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
- B2: campaign.json, state machine, caption builder + tag test.
