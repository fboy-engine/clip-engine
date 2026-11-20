"""Plain Python CLI. Runner-agnostic: no GitHub-specific code in here.

Usage: python -m clip_engine.cli <command> [args]
"""
import os
import sys

from . import gemini


def gemini_check():
    """Probe the top flash models once each. Never prints the key."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("FAIL: GEMINI_API_KEY is not set in this environment")
        return 1
    timeout = int(os.environ.get("GEMINI_PROBE_TIMEOUT", "30"))
    try:
        ranked = gemini.rank_models(gemini.list_models(key))
    except Exception as e:
        print(f"FAIL: ListModels: {type(e).__name__}")
        return 1
    print("candidates: " + (", ".join(ranked[:4]) or "none"))
    ok_any = False
    for m in ranked[:2] + ["gemini-flash-latest"]:
        ok, detail, secs = gemini.probe(key, m, timeout=timeout)
        print(f"{'OK  ' if ok else 'FAIL'} {m}: {detail} ({secs:.1f}s)")
        ok_any = ok_any or ok
    print("PASS" if ok_any else "FAIL")
    return 0 if ok_any else 1


COMMANDS = {"gemini-check": gemini_check}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print("commands: " + ", ".join(COMMANDS))
        return 2
    return COMMANDS[argv[0]]()


if __name__ == "__main__":
    sys.exit(main())
