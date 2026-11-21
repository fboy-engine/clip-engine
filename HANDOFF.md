# HANDOFF

Session started 2026-11-21 16:13 UTC. Plan: Stage 0 identity + first push, then check Stage 1 state, then Stage 2 (cam detect + layout + overlay gate), Stage 3 (tracking, trim, large-v3, summary, layout.json), Stage 4 (cache_only verification run).

## DONE
- Stage 0: first push (this file).

## IN PROGRESS
- Reading repo state (STATUS.md read; Stage 1 status unverified).

## NEXT
1. Check whether Stage 1 (font, caption lane, f*** mask, hook rules) already exists.
2. Stage 2: cam detection seed + face verify, top block, gameplay crop, exclusion zones, overlay gate.
3. Stage 3: tracking, trim, large-v3, run summary, layout.json.

## KNOWN BREAKS
- Last real run (4488260530) failed the opening-freeze gate; no facecam found in corners (see STATUS.md).

## RUN STATE
- Last run: 4488260530, render exited 1 (opening freeze FAIL).
