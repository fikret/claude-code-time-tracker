#!/usr/bin/env python3
"""
install-statusline.py — enable the Time Tracker status line.

Claude Code plugins cannot register the main status line themselves (only the
`agent` and `subagentStatusLine` keys are honored in a plugin's settings.json).
So this installer copies the tracker scripts to a stable location and adds a
`statusLine` entry to the user's ~/.claude/settings.json pointing at it.

Stable location (survives plugin updates): ~/.claude/time-tracker/

Run via:  /time-tracker:setup
or:       python3 scripts/install-statusline.py
Undo:     python3 scripts/install-statusline.py --uninstall
"""

import os
import sys
import json
import shutil

HOME = os.path.expanduser("~")
DEST = os.path.join(HOME, ".claude", "time-tracker")
CACHE = os.path.join(HOME, ".claude", ".cc-time-cache")
SETTINGS = os.path.join(HOME, ".claude", "settings.json")
SRC = os.path.dirname(os.path.abspath(__file__))


def load_settings():
    try:
        with open(SETTINGS, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"! Could not parse {SETTINGS}: {e}", file=sys.stderr)
        sys.exit(1)


def save_settings(data):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, SETTINGS)


def install():
    os.makedirs(DEST, exist_ok=True)
    for name in ("statusline.py", "ccstats.py"):
        shutil.copy2(os.path.join(SRC, name), os.path.join(DEST, name))

    command = (f'python3 "{os.path.join(DEST, "statusline.py")}" "{CACHE}"')
    settings = load_settings()
    existing = settings.get("statusLine")
    settings["statusLine"] = {"type": "command", "command": command}
    save_settings(settings)

    print("✓ Time Tracker status line installed.")
    print(f"  scripts : {DEST}")
    print(f"  settings: {SETTINGS} (statusLine)")
    if existing and existing.get("command", "").find("statusline.py") < 0:
        print("  note    : replaced an existing statusLine — previous value:")
        print(f"            {json.dumps(existing)}")
    print("\nRestart Claude Code (or start a new session) to see it at the bottom.")


def uninstall():
    settings = load_settings()
    sl = settings.get("statusLine", {})
    if isinstance(sl, dict) and "time-tracker" in sl.get("command", "") + \
            ("statusline.py" if "statusline.py" in sl.get("command", "") else ""):
        settings.pop("statusLine", None)
        save_settings(settings)
        print("✓ Removed Time Tracker statusLine from settings.json.")
    else:
        print("statusLine is not managed by Time Tracker; left unchanged.")
    print(f"(Scripts remain in {DEST}; delete manually if you want them gone.)")


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install()
