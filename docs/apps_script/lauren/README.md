# Lauren Hamden control panel (Apps Script web app)

A private, phone-first page for the Lauren Hamden campaign. The Google Sheet stays the database; the engine (GitHub Actions) keeps reading and writing the same cells, and this panel only looks at them and flips three of them.

What you get: campaign state, the next clip with a COPY caption button, an APPROVE button (opens when the engine says so, one confirm tap), the posted Reel link with a "submit to Reach before HH:MM" countdown, an "Open Reach submit page" button (opens only, never submits), ADD LINK when every clip is used, the queue, and the last 10 log rows. It refreshes itself every 10 seconds. Times are Asia/Karachi.

## Files (paste all three into the existing clip-engine Apps Script project)

- Code.gs: https://raw.githubusercontent.com/fboy-engine/clip-engine/main/docs/apps_script/lauren/Code.gs
- Index.html: https://raw.githubusercontent.com/fboy-engine/clip-engine/main/docs/apps_script/lauren/Index.html
- appsscript.json: https://raw.githubusercontent.com/fboy-engine/clip-engine/main/docs/apps_script/lauren/appsscript.json

Phone tip: open the raw link in Chrome, tap and hold the text, Select all, Copy, then paste into the matching file in the Apps Script editor (use Chrome's "Desktop site" for script.google.com).

## First-time setup (phone)

1. Open script.google.com and open the project **clip-engine** (a standalone project; the code opens the sheet by id, so it needs no bound sheet).
2. Project Settings (gear icon): tick **Show "appsscript.json" manifest file in editor**.
3. Back in Editor: open appsscript.json, select all, paste the manifest. Open Code.gs, select all, paste Code.gs. Tap **+** next to Files, choose **HTML**, name it exactly Index (the editor adds .html), paste Index.html. Save.
4. **Deploy > New deployment**. Tap the gear next to "Select type" and choose **Web app**.
   - Execute as: **Me**
   - Who has access: **Only myself**
   - Tap **Deploy**, then **Authorize access**, pick your Google account and allow it. The only permission asked is Google Sheets.
5. Copy the **Web app URL** (ends with /exec). Open it in Chrome while signed in to the same Google account. Chrome menu > **Add to Home screen**.

## Updating later (the URL stays the same)

Paste the new file contents, Save, then **Deploy > Manage deployments**, pencil icon, Version: **New version**, Deploy. Do not use "New deployment" again: that creates a different URL.

## The sheet must stay unshared

The page runs as you and reads the sheet with your Google account, so the sheet never needs to be shared with anyone. Keep it that way: anyone with edit access could tick APPROVE. The engine reaches the sheet through the Composio connection, not through sharing. Web app access is "Only myself", so the page only opens when you are signed in to your account.

## What it can write

Only three cells on the Campaign tab: APPROVE (B7), Add link (B12) and Confirm add link (B13). setCell_() in Code.gs is the only write path and throws for any other cell. The engine is unchanged.

- **APPROVE** writes B7 = TRUE only if, under a script lock, the sheet still shows the same clip as AWAITING_APPROVAL, the approve time has passed and APPROVE is not already ticked. Stale or double taps are refused with a message. The posting itself still happens on the engine's next run (nothing uploads while dry_run is on and the uploader is not enabled).
- **ADD LINK** is shown only when every clip is used (same rule as the engine). It accepts an https twitch.tv or youtube.com link up to 300 characters and writes B12 then B13, exactly like typing in the sheet. Note: the engine's own link check (sheet.TWITCH_URL) currently accepts Twitch links only, so a YouTube link is passed on but logged as ADD_LINK_REJECTED until that regex is widened.

All text from the sheet is rendered with textContent (no HTML injection, no eval).

## One shared cell map

The cell map (TABS, CELLS, QUEUE_LAST_ROW, QUEUE_COLS) lives once in Code.gs, between CELL_MAP_BEGIN and CELL_MAP_END, and mirrors campaign_lib/sheet.py. tests/test_apps_script_panel.py fails if they differ, so change sheet.py first and then Code.gs. The same test checks the sheet id, timezone, Reach url and 30 min submit window against campaign.json.

## Tests

python -m unittest discover -s tests (runs in the unit-tests workflow) includes the cell-map and safety checks and runs node tests/apps_script_panel_test.js: state-to-button logic, countdown format, link validation, and the server functions against a fake spreadsheet (writes only the allowed cells, stale and double taps write nothing).

## If something looks wrong

- "offline, retrying" banner: the page could not reach the script (no signal, or you are signed out). It keeps retrying every 10 s.
- A Google sign-in or "Authorization required" page: open the /exec URL while signed in to the deploying account.
- "Sheet tabs Campaign/Queue/Log not found": SHEET_ID in Code.gs is wrong or the tabs were renamed.
