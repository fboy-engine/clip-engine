"""Post the clip you approved on the review page (Review tab row with state APPROVED).

Runs every few minutes (pool-post.yml), separate from the campaign loop. DRY RUN unless the Campaign cell
real_upload (B23) says exactly ONE:POOL; that switch is single use (cleared before the upload).
Dry run posts nothing: it prints the redacted request lines and writes a clearly fake link (DRYRUN) so the page can
show Copy link. Review columns: file_id, name, state, link. States: APPROVED -> POSTING -> POSTED | FAILED | UNCERTAIN.
An UNCERTAIN post is never retried by this job."""
import os
import re
import subprocess
import sys
import tempfile
import time

from . import caption as capmod
from . import composio, config, ig_composio, instagram, poster, states
from .editor import FALLBACK_LINE

REVIEW = "Review"
SWITCH_CELL = "Campaign!B23"
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.mp4$")


def read(client, sid):
    data = client.execute("GOOGLESHEETS_BATCH_GET", {
        "spreadsheet_id": sid, "valueRenderOption": "UNFORMATTED_VALUE",
        "ranges": ["%s!A1:E300" % REVIEW, SWITCH_CELL]})
    vr = composio.find_key(data, "valueRanges") or []
    rows = (vr[0].get("values") if vr else None) or []
    sw = ((vr[1].get("values") if len(vr) > 1 else None) or [[""]])[0]
    return rows, (str(sw[0]) if sw else "")


def write(client, sid, rng, values):
    client.execute("GOOGLESHEETS_UPDATE_VALUES_BATCH", {
        "spreadsheet_id": sid, "valueInputOption": "RAW", "data": [{"range": rng, "values": values}]})


class _NoClient:
    def execute(self, *a, **k):
        raise RuntimeError("dry run: no Composio call")


def gh_download(name, outdir):
    subprocess.run(["gh", "release", "download", "pool", "-p", name, "-D", outdir, "--clobber"], check=True)
    return os.path.join(outdir, name)


def run(client=None, cfg=None, env=None, download=gh_download, uploader_cls=None, log=print):
    env = os.environ if env is None else env
    cfg = cfg or config.load()
    client = client or composio.Client()
    sid = (cfg.get("sheet") or {}).get("id")
    try:
        rows, switch = read(client, sid)
    except composio.ComposioError as e:
        log("POOLPOST no Review tab yet or sheet unreadable: %s" % str(e)[:120])
        return "no-review"
    row = next((i for i, r in enumerate(rows) if i > 0 and len(r) > 2 and str(r[2]).strip() == "APPROVED"), None)
    if row is None:
        log("POOLPOST nothing approved")
        return "idle"
    num, name = row + 1, str(rows[row][1]).strip()
    rng = "%s!C%d:D%d" % (REVIEW, num, num)
    if not NAME_RE.match(name):
        write(client, sid, rng, [["FAILED", "bad file name"]])
        log("POOLPOST FAILED bad file name")
        return "failed"
    write(client, sid, rng, [["POSTING", ""]])                # intent first: a crash after this is never re-posted
    mode = switch.strip().upper()
    real = mode in ("ONE:POOL", "ON:POOL")
    try:
        path = download(name, tempfile.mkdtemp())
        item = states.Item(n=9000 + num, source="pool:" + name, kind="pool",
                           caption=capmod.build_caption(cfg, FALLBACK_LINE), file=path)
    except Exception as e:
        write(client, sid, rng, [["FAILED", "prepare: %s" % type(e).__name__]])
        log("POOLPOST FAILED prepare %s" % type(e).__name__)
        return "failed"
    if uploader_cls is not None:
        up = uploader_cls(env.get("IG_ACCESS_TOKEN_LAUREN", ""), env.get("IG_USER_ID_LAUREN", ""), real=real)
    else:                                                     # the user's Composio Instagram connection (default)
        up = ig_composio.ComposioInstagramUploader(None if real else _NoClient(), real=real)
    if not real:
        for line in up.plan(item):
            log("DRYRUN_REQUEST " + line)
        link = "https://www.instagram.com/reel/DRYRUN%d/" % int(time.time())
        write(client, sid, rng, [["POSTED", link]])
        log("POOLPOST DRY RUN done: nothing was sent (real_upload switch is not ONE:POOL)")
        return "dryrun"
    if mode == "ONE:POOL":
        write(client, sid, SWITCH_CELL, [[""]])               # one-shot form: gone before the upload starts (ON:POOL stays)
    try:
        _mid, link = up.post(item, poster.new_token(item.n))
    except poster.UploadRejected as e:
        write(client, sid, rng, [["FAILED", str(e)[:80]]])
        log("POOLPOST FAILED rejected: %s" % str(e)[:80])
        return "failed"
    except Exception as e:
        write(client, sid, rng, [["UNCERTAIN", type(e).__name__]])
        log("POOLPOST UNCERTAIN %s (no retry, check Instagram by hand)" % type(e).__name__)
        return "uncertain"
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())   # the page starts its 150 min from this moment
    write(client, sid, "%s!C%d:E%d" % (REVIEW, num, num), [["POSTED", link, stamp]])
    log("POOLPOST POSTED")
    return "posted"


if __name__ == "__main__":
    sys.exit(0 if run() in ("idle", "dryrun", "posted", "no-review", "failed", "uncertain") else 1)
