"""Per-stage wall time and memory log, stage time budgets and a memory guard for the render.

Why: two renders (runs 45816206788 and 45787748388) stalled in the Render step with no log. GitHub's own 35 min step
timeout never fired in either, so the whole runner VM was starved (the annotation says so), not just one Python
call. This module (1) writes out/render_stages.json and a partial out/render.json after EVERY stage, (2) stops the
render with a clear "stage X timed out after N min" line when a stage or the whole render runs over its budget,
and (3) stops it before the VM freezes when free memory runs out. A hard os._exit is used because the main thread
may be inside native code (ctranslate2, onnxruntime, ffmpeg wait) that no exception can interrupt.
"""
import ctypes
import gc
import json
import os
import sys
import threading
import time

TOTAL_BUDGET_SEC = 31 * 60        # under the 35 min step timeout of clip.yml, which is under the 50 min job limit
MIN_SWAP_FREE_MB = 2048           # the guard only fires when swap is nearly gone too (clip.yml has 10 GB swap: RAM alone being full is not fatal)
MIN_AVAIL_MB = 500                # MemAvailable below this for STRIKES polls in a row = stop before the runner freezes
STRIKES = 5                       # (clip.yml has 10 GB swap now: only a sustained shortage counts, not a dip)
POLL_SEC = 1.0
EXIT_TIMEOUT, EXIT_MEMORY = 124, 125


def _kb(path, key):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith(key):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def rss_mb():
    v = _kb("/proc/self/status", "VmRSS:")
    return None if v is None else round(v / 1024.0, 1)


def peak_rss_mb():
    v = _kb("/proc/self/status", "VmHWM:")
    return None if v is None else round(v / 1024.0, 1)


def mem_available_mb():
    v = _kb("/proc/meminfo", "MemAvailable:")
    return None if v is None else round(v / 1024.0, 1)


def swap_free_mb():
    v = _kb("/proc/meminfo", "SwapFree:")
    return None if v is None else round(v / 1024.0, 1)


def release_memory():
    """Drop Python garbage and ask glibc to hand freed heap pages back to the OS (a deleted model otherwise keeps its
    RSS and the next model stacks on top of it). Returns the RSS afterwards."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass
    return rss_mb()


class Watch:
    def __init__(self, out_dir, total_budget=TOTAL_BUDGET_SEC, min_avail_mb=MIN_AVAIL_MB, strikes=STRIKES, poll=POLL_SEC,
                 clock=time.monotonic, avail_fn=mem_available_mb, rss_fn=rss_mb, peak_fn=peak_rss_mb,
                 exit_fn=os._exit, log=print, swap_fn=swap_free_mb, min_swap_free_mb=MIN_SWAP_FREE_MB):
        self.out_dir, self.total_budget, self.min_avail, self.strikes_max = out_dir, total_budget, min_avail_mb, strikes
        self.poll, self.clock, self.avail_fn, self.rss_fn, self.peak_fn = poll, clock, avail_fn, rss_fn, peak_fn
        self.exit_fn, self.log = exit_fn, log
        self.swap_fn, self.min_swap_free = swap_fn, min_swap_free_mb
        self.t0 = clock()
        self.stages, self.cur, self.strikes, self.aborted = [], None, 0, None
        self.lock = threading.RLock()
        self._thread, self._stop = None, threading.Event()

    # ---- stages
    def begin(self, name, budget_sec):
        with self.lock:
            self._end_locked("ok")
            now = self.clock()
            self.cur = {"stage": name, "budget_sec": budget_sec, "t": now, "rss_start_mb": self.rss_fn(),
                        "min_avail_mb": self.avail_fn()}
            self.log(f"[stage] {name} start (budget {budget_sec / 60:.0f} min, rss {self.cur['rss_start_mb']} MB, "
                     f"avail {self.cur['min_avail_mb']} MB)")
            self._flush_locked()

    def _end_locked(self, status):
        c = self.cur
        if c is None:
            return
        wall = self.clock() - c["t"]
        row = {"stage": c["stage"], "status": status, "wall_sec": round(wall, 1), "budget_sec": c["budget_sec"],
               "rss_start_mb": c["rss_start_mb"], "rss_end_mb": self.rss_fn(), "peak_rss_mb": self.peak_fn(),
               "min_avail_mb": c["min_avail_mb"]}
        self.stages.append(row)
        self.cur = None
        self.log(f"[stage] {row['stage']} {status} in {row['wall_sec']} s, rss {row['rss_start_mb']} -> {row['rss_end_mb']} MB "
                 f"(process peak {row['peak_rss_mb']} MB), lowest free memory {row['min_avail_mb']} MB")
        self._flush_locked()

    def end(self, status="ok"):
        with self.lock:
            self._end_locked(status)

    def close(self, status="ok"):
        """End the current stage and stop the watchdog. status 'error: ...' when the render raised."""
        with self.lock:
            self._end_locked(status)
            self._stop.set()

    # ---- the checks (pure, called by the thread; tests call them directly)
    def check(self):
        """Returns (reason, exit code) if the render must stop now, else None. Also tracks the lowest free memory."""
        with self.lock:
            now = self.clock()
            avail = self.avail_fn()
            c = self.cur
            if c is not None and avail is not None and (c["min_avail_mb"] is None or avail < c["min_avail_mb"]):
                c["min_avail_mb"] = avail
            if now - self.t0 > self.total_budget:
                return (f"render timed out after {self.total_budget / 60:.0f} min in total"
                        + (f" (in stage {c['stage']})" if c else ""), EXIT_TIMEOUT)
            if c is not None and now - c["t"] > c["budget_sec"]:
                return (f"stage {c['stage']} timed out after {c['budget_sec'] / 60:.0f} min (budget for this stage)", EXIT_TIMEOUT)
            swap = self.swap_fn()
            if avail is not None and avail < self.min_avail and (swap is None or swap < self.min_swap_free):
                self.strikes += 1
            else:
                self.strikes = 0
            if self.strikes >= self.strikes_max:
                return (f"stage {c['stage'] if c else '-'}: memory exhausted (free {avail} MB, swap free {swap} MB, process rss {self.rss_fn()} MB), "
                        f"stopping before the runner freezes", EXIT_MEMORY)
            return None

    def abort(self, reason, code):
        with self.lock:
            self.aborted = reason
            self._end_locked("aborted: " + reason)
            self._flush_locked(extra={"aborted": reason})
        self.log("FAIL render: " + reason)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        self.exit_fn(code)

    def _run(self):
        while not self._stop.wait(self.poll):
            r = self.check()
            if r:
                self.abort(*r)
                return

    def start(self):
        self._thread = threading.Thread(target=self._run, name="render-watchdog", daemon=True)
        self._thread.start()
        return self

    # ---- evidence on disk
    def _flush_locked(self, extra=None):
        doc = {"partial": True, "stages": list(self.stages), "current": None if self.cur is None else self.cur["stage"],
               "elapsed_sec": round(self.clock() - self.t0, 1), "total_budget_sec": self.total_budget}
        if extra:
            doc.update(extra)
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            self._write("render_stages.json", doc)
            p = os.path.join(self.out_dir, "render.json")
            keep = False
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        keep = not json.load(f).get("partial", False)
                except (OSError, ValueError):
                    keep = False
            if not keep:                      # never overwrite a finished render.json with the partial one
                self._write("render.json", doc)
        except OSError:
            pass

    def _write(self, name, doc):
        p = os.path.join(self.out_dir, name)
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)


_current = None


def start(out_dir, **kw):
    global _current
    if _current is not None:
        _current.close("replaced")
    _current = Watch(out_dir, **kw).start()
    return _current


def current():
    return _current


def begin(name, budget_sec):
    if _current is not None:
        _current.begin(name, budget_sec)


def close(status="ok"):
    global _current
    if _current is not None:
        _current.close(status)


def stages():
    return list(_current.stages) if _current is not None else []
