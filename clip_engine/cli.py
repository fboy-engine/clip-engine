"""Plain Python CLI. Runner-agnostic: no GitHub-specific code in here.

Usage: python -m clip_engine.cli <command> [args]
"""
import os
import sys

from . import gemini


def gemini_check():
    """ONE real Gemini call. Prints model name and a short reply, never the key."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("FAIL: GEMINI_API_KEY is not set in this environment")
        return 1
    try:
        model = gemini.resolve_model(key)
        print(f"model resolved: {model}")
        text = gemini.generate("Reply with exactly the single word: OK", key=key,
                               retries=3, timeout=45)
    except gemini.GeminiUnavailable as e:
        print(f"FAIL: GeminiUnavailable: {e}")
        return 1
    except Exception as e:  # never let the key leak through a traceback
        print(f"FAIL: {type(e).__name__}")
        return 1
    print(f"reply: {text.strip()[:40]!r}")
    print("PASS")
    return 0


COMMANDS = {"gemini-check": gemini_check}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print("commands: " + ", ".join(COMMANDS))
        return 2
    return COMMANDS[argv[0]]()


if __name__ == "__main__":
    sys.exit(main())
