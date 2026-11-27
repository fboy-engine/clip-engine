# Clip review page (lauren2)

Replaces docs/apps_script/lauren (Code.gs, Index.html, appsscript.json). Same project, same /exec URL: paste the three files over the old ones, Save, then Deploy > Manage deployments > pencil > New version (keep the same deployment). Authorize Drive when asked.

Raw links:
- https://raw.githubusercontent.com/fboy-engine/clip-engine/main/docs/apps_script/lauren2/Code.gs
- https://raw.githubusercontent.com/fboy-engine/clip-engine/main/docs/apps_script/lauren2/Index.html
- https://raw.githubusercontent.com/fboy-engine/clip-engine/main/docs/apps_script/lauren2/appsscript.json

How it works: the Drive folder `ClipPool` (created on first open) holds the clips waiting. Each clip plays on the page. Approve (tap twice) hides the rest and ticks Campaign!B7, adds a row in the `Review` tab (file id, name, APPROVED), shows "Wait for the link...", then Copy link when Campaign!B10 gets a new instagram link; the file is then deleted from Drive. Keep marks the file "saved" (stays in the list next round). Delete (tap twice) trashes it.

NOT wired yet (engine side): (1) the engine must save every PASSING clip into ClipPool (Drive); (2) on APPROVED it must upload the file whose id is in the Review tab and write the permalink to Campaign!B10. Until then the page shows whatever mp4 you drop in ClipPool.
