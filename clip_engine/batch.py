"""Batch over the whole stream and the short clips: plan the windows, then collect every result. Stdlib only.

python -m clip_engine.batch finds   -> matrix of find jobs
python -m clip_engine.batch renders -> matrix of render jobs from the find artifacts
python -m clip_engine.batch summary -> results.json + RESULTS.md from the render artifacts and the run's jobs
Every render is a dry run through clip.yml. No gate lives here: this file only chooses windows and reads logs.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

VOD = "https://m.twitch.tv/videos/2934045907"
VOD_LEN = 3772
SECTIONS = [(0, 600), (600, 1200), (1200, 1800), (1800, 2400), (2100, 2700), (2400, 3000), (3000, 3600), (3172, VOD_LEN)]
CAMPAIGN = "campaigns/lauren-hamden/campaign.json"
PAD, MIN_WINDOW, TOP = 14, 54, 5
REGRESSION = {"tag": "regression", "source": "stream 2465-2519 s (known passing window)", "url": VOD,
              "start_sec": "2100", "end_sec": "2700", "render_start": "365", "render_end": "419",
              "dense_cut": True, "cam_rect": ""}


def _round():
    try:
        return int(os.environ.get("BATCH_ROUND", "0") or 0)
    except ValueError:
        return 0


def finds(campaign, rnd=None):
    rnd = _round() if rnd is None else rnd
    jobs = [{"tag": f"s{a}", "kind": "stream", "url": VOD, "start_sec": str(a), "end_sec": str(b), "cam_rect": "",
             "top": str(TOP if rnd == 0 else 10 + 5 * rnd)} for a, b in SECTIONS]
    if rnd:
        return jobs                                   # short clips and the regression window are round 0 only
    jobs += [{"tag": f"c{s['n']}", "kind": "clip", "url": s["url"], "start_sec": "0", "end_sec": "0",
              "cam_rect": s.get("cam_rect", ""), "top": str(TOP)}
             for s in campaign["sources"] if s.get("kind") == "clip"]
    return jobs


def window(start, end, duration):
    """Candidate padded by PAD s each side, at least MIN_WINDOW wide, kept inside the file."""
    a, b = start - PAD, end + PAD
    if b - a < MIN_WINDOW:
        extra = (MIN_WINDOW - (b - a)) / 2
        a, b = a - extra, b + extra
    if a < 0:
        a, b = 0, b - a
    if duration and b > duration:
        a, b = max(0, a - (b - duration)), duration
    return int(a), int(b * 10) / 10          # round down so the window never passes the end of the file


def _num(x):
    return f"{x:g}"


DONE_FILE = "campaigns/lauren-hamden/batch_done.json"
_WIN = re.compile(r"stream (\d+(?:\.\d+)?)-(\d+(?:\.\d+)?) s")


def done_windows(paths=("docs/BATCH_RESULTS.md", "docs/BATCH_RESULTS_run1.md")):
    """Absolute stream windows already rendered (earlier batches), so a later round never repeats one."""
    out = []
    for pth in paths:
        try:
            with open(pth, encoding="utf-8") as f:
                out += [(float(a), float(b)) for a, b in _WIN.findall(f.read())]
        except OSError:
            pass
    old = _json(DONE_FILE)
    if old:
        out += [(float(a), float(b)) for a, b in old]
    return out


def _overlaps(a, b, wins):
    return any(a < d and c < b for c, d in wins)


def renders(find_jobs, load, done=None, rnd=0):
    """load(job) -> (candidates best first or None, file duration or None). Returns (render jobs, skipped rows)."""
    jobs, skipped = ([dict(REGRESSION)] if not rnd else []), []
    seen = list(done or [])
    picked = []
    for f in find_jobs:
        cands, dur = load(f)
        label = f"clip #{f['tag'][1:]}" if f["kind"] == "clip" else f"stream section {f['start_sec']}-{f['end_sec']} s"
        if not cands:
            why = "find gave no candidate window" if cands is not None else "find did not finish (see its job)"
            skipped.append({"tag": f["tag"], "source": label, "window": "-", "result": "SKIP" if cands is not None else "CRASH",
                            "gate": "find", "detail": why, "grade": "", "artifact": ""})
            continue
        if f["kind"] == "stream" and not dur:
            dur = float(f["end_sec"]) - float(f["start_sec"])
        picks = cands[:1] if f["kind"] == "clip" else cands
        n = 0
        for i, c in enumerate(picks, 1):
            a, b = window(c["start"], c["end"], dur)
            if f["kind"] == "stream":
                off = float(f["start_sec"])
                if n >= TOP or _overlaps(off + a, off + b, seen if rnd else []) or (rnd and _overlaps(off + a, off + b, picked)):
                    continue                          # already rendered in an earlier round, or enough for this section
                picked.append((off + a, off + b))
                n += 1
            for dense in ((False, True) if f["kind"] == "clip" else (True,)):
                if f["kind"] == "clip":
                    tag, src = f"{f['tag']}-{'dense' if dense else 'plain'}", label
                else:
                    off = float(f["start_sec"])
                    tag, src = f"{f['tag']}-r{rnd}w{i}" if rnd else f"{f['tag']}-w{i}", f"stream {off + a:g}-{off + b:g} s"
                jobs.append({"tag": tag, "source": src, "url": f["url"], "start_sec": f["start_sec"],
                             "end_sec": f["end_sec"], "render_start": _num(a), "render_end": _num(b),
                             "dense_cut": dense, "cam_rect": f["cam_rect"]})
    only = {t.strip() for t in os.environ.get("BATCH_ONLY", "").split(",") if t.strip()}
    if only:                                   # resume: render only these tags, never redo finished windows
        jobs = [j for j in jobs if j["tag"] in only]
    return jobs, skipped


GATES = [("tracking gate", "tracking"), ("panel check", "panel check"), ("captions check", "captions"),
         ("no facecam", "no facecam"), ("cam-bench", "cam benchmark"), ("rendered output gate", "rendered output"), ("overlay", "overlay"),
         ("cam quality", "cam quality"), ("cam enhance", "cam enhance"),
         ("true peak", "loudness"), ("dense cut", "cut planner"), ("no start in", "cut planner"),
         ("cut point", "cut planner"), ("shorter than", "too short"), ("clip would be", "too short"),
         ("frozen or silent", "opening"), ("silent", "silent"), ("hook", "hook")]


def gate_of(line):
    low = line.lower()
    return next((name for key, name in GATES if key in low), "other")


def classify(log, bench_error, conclusion, annotations):
    """(result, gate, detail, grade line). A PASS needs both a PASS grade and a green job."""
    lines = (log or "").splitlines()
    grade = next((l.strip() for l in reversed(lines) if l.startswith("GRADE:")), "")
    note = " | ".join(annotations)[:400]
    fail = next((l.strip() for l in lines if l.startswith("FAIL render:")), "")
    if fail:
        if "timed out" in fail:
            return "TIMEOUT", "timeout", fail, grade
        if "memory exhausted" in fail:
            return "CRASH", "memory", fail, grade
        return "FAIL", gate_of(fail), fail, grade
    if grade.startswith("GRADE: FAIL"):
        bad = [l.strip() for l in lines if l.startswith("FAIL  ")]
        name = bad[0][6:].split(":", 1)[0] if bad else "?"
        return "FAIL", f"grade: {name}", " ; ".join(bad) or grade, grade
    if grade.startswith("GRADE: PASS") and conclusion == "success":
        return "PASS", "", "", grade
    if bench_error:
        return "FAIL", gate_of(bench_error), bench_error.splitlines()[0], grade
    if re.search(r"exceeded the maximum|timed out|has timed out", note, re.I):
        return "TIMEOUT", "timeout", note, grade
    return "CRASH", "crash", note or f"job conclusion: {conclusion or 'unknown'}, no verdict in the log", grade


def _cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")[:600]


def markdown(rows, run_url):
    count = lambda r: sum(x["result"] == r for x in rows)
    gates = {}
    for x in rows:
        if x["result"] != "PASS":
            gates[x["gate"]] = gates.get(x["gate"], 0) + 1
    reg = next((x for x in rows if x["tag"] == "regression"), None)
    out = ["# Batch results (dry run, nothing was uploaded)", "", f"Run: {run_url}", "",
           f"**PASS {count('PASS')} / FAIL {count('FAIL')} / SKIP {count('SKIP')} / CRASH {count('CRASH')} / "
           f"TIMEOUT {count('TIMEOUT')}** out of {len(rows)}", "",
           f"Regression window (must pass): **{reg['result'] if reg else 'MISSING'}**", "",
           "Passing windows from overlapping stream times can be the same moment: watch them before using two.", "",
           "## Not passed, by gate", ""]
    out += [f"- {g}: {n}" for g, n in sorted(gates.items(), key=lambda kv: (-kv[1], kv[0]))] or ["- none"]
    out += ["", "## Every window", "", "| job | source | window in file (s) | result | gate line | grade | artifact |",
            "|---|---|---|---|---|---|---|"]
    order = {"PASS": 0, "FAIL": 1, "SKIP": 2, "CRASH": 3, "TIMEOUT": 4}
    for x in sorted(rows, key=lambda x: (order.get(x["result"], 9), x["tag"])):
        out.append("| " + " | ".join(_cell(x[k]) for k in ("tag", "source", "window", "result", "detail", "grade", "artifact")) + " |")
    return "\n".join(out) + "\n"


def _read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _json(path):
    try:
        return json.loads(_read(path) or "")
    except ValueError:
        return None


def _api(path):
    req = urllib.request.Request(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{path}",
                                 headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def run_jobs(run_id, api=_api):
    """{render tag: (conclusion, [failure annotations])}. Never raises: the report is written even without the API."""
    out = {}
    try:
        page = 1
        while True:
            got = api(f"/actions/runs/{run_id}/jobs?per_page=100&page={page}")["jobs"]
            for j in got:
                m = re.match(r"render (\S+) / clip$", j["name"])
                if not m:
                    continue
                notes = []
                if j.get("conclusion") != "success":
                    try:
                        notes = [a["message"] for a in api(f"/check-runs/{j['id']}/annotations")
                                 if a.get("annotation_level") == "failure"]
                    except Exception as e:
                        notes = [f"annotations unavailable: {type(e).__name__}"]
                out[m.group(1)] = (j.get("conclusion"), notes)
            if len(got) < 100:
                return out
            page += 1
    except Exception as e:
        print(f"jobs API unavailable: {type(e).__name__}: {str(e)[:200]}")
        return out


def summarize(jobs, skipped, art_dir, run_id, states):
    rows = list(skipped)
    for j in jobs:
        name = f"clip-{run_id}-{j['tag']}"
        d = os.path.join(art_dir, name)
        log = _read(os.path.join(d, "render_step.log"))
        bench = (_json(os.path.join(d, "cam_bench.json")) or {}).get("error")
        if bench and not str(bench).startswith("FAIL"):
            bench = f"FAIL cam-bench: {bench}"
        conclusion, notes = states.get(j["tag"], (None, []))
        if conclusion is None and log is not None and "GRADE: PASS" in log:
            notes = notes + ["job state unknown (jobs API)"]
        result, gate, detail, grade = classify(log, bench, conclusion, notes)
        rows.append({"tag": j["tag"], "source": j["source"],
                     "window": f"{j['render_start']}-{j['render_end']}{' dense' if j['dense_cut'] else ''}",
                     "result": result, "gate": gate, "detail": detail, "grade": grade,
                     "artifact": name if os.path.isdir(d) else ""})
    return rows


def _output(name, value):
    line = f"{name}={json.dumps(value, separators=(',', ':'))}"
    print(line)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(line + "\n")


def main(argv=None):
    p = argparse.ArgumentParser(prog="clip_engine.batch")
    p.add_argument("cmd", choices=["finds", "renders", "summary", "next"])
    p.add_argument("--artifacts", default="arts")
    p.add_argument("--plan", default="plan.json")
    p.add_argument("--out", default="batch_out")
    a = p.parse_args(argv)
    run_id = os.environ.get("GITHUB_RUN_ID", "0")
    campaign = _json(CAMPAIGN)
    if a.cmd == "finds":
        _output("matrix", finds(campaign))
        return 0
    if a.cmd == "renders":
        def load(f):
            c = _json(os.path.join(a.artifacts, f"find-{run_id}-{f['tag']}", "candidates.json"))
            pr = _json(os.path.join(a.artifacts, f"dl-check-{run_id}-{f['tag']}", "probe.json")) or {}
            if c is not None:
                return c["candidates"], pr.get("duration")
            # find exits 1 without a file when there is no window to rank (short or flat source)
            if pr.get("duration") and pr["duration"] < 25:
                return [], pr["duration"]
            return None, pr.get("duration")
        jobs, skipped = renders(finds(campaign), load, done_windows(), _round())
        with open(a.plan, "w") as f:
            json.dump({"jobs": jobs, "skipped": skipped}, f, indent=1)
        _output("matrix", jobs)
        return 0
    if a.cmd == "next":                               # after a round: record its windows, say whether another round has work
        plan = _json(a.plan) or {"jobs": []}
        new = []
        for j in plan["jobs"]:
            if j["tag"] == "regression" or "render_start" not in j:
                continue
            o = float(j["start_sec"])
            new.append((o + float(j["render_start"]), o + float(j["render_end"])))
        old = _json(DONE_FILE) or []
        os.makedirs(os.path.dirname(DONE_FILE), exist_ok=True)
        with open(DONE_FILE, "w") as f:
            json.dump(old + [list(w) for w in new if list(w) not in old], f)
        print("NEXT_ROUND", "yes" if new else "no", len(new))
        return 0 if new else 3
    plan = _json(a.plan)
    os.makedirs(a.out, exist_ok=True)
    url = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{run_id}"
    if plan is None:
        rows = [{"tag": "plan", "source": "-", "window": "-", "result": "CRASH", "gate": "plan",
                 "detail": "the plan step produced no job list", "grade": "", "artifact": ""}]
    else:
        rows = summarize(plan["jobs"], plan["skipped"], a.artifacts, run_id, run_jobs(run_id))
    md = markdown(rows, url)
    with open(os.path.join(a.out, "results.json"), "w") as f:
        json.dump({"run": url, "results": rows}, f, indent=1)
    with open(os.path.join(a.out, "RESULTS.md"), "w") as f:
        f.write(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md[:900000])
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
