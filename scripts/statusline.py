#!/usr/bin/env python3
"""
statusline.py — Claude Code status line for the Time Tracker plugin.

Reads the status-line JSON payload from stdin and prints a single compact,
ANSI-colored line summarizing time & tokens for the CURRENT project:

  ⏱ elabRandevu · 12h5m total · 45m today · 23m session · 1.4B tok ~$2.8k

"session" is the ACTIVE time of the current session (same gap rule as
"total"), not wall-clock: an idle or sleeping, still-open window adds nothing.

The active project is identified from `transcript_path` (its parent directory
is the project's transcript folder). All heavy lifting is cached per-file by
ccstats, so this stays fast enough to run on every refresh.

Config via env vars:
  CC_TIME_GAP        break threshold in minutes (default 5)
  CC_TIME_SHOW_COST  "0" to hide token/cost segment (default show)
  CC_TIME_ICON       leading icon (default "⏱")

Never fails loudly: on any error it prints nothing and exits 0 so it can
never disrupt Claude Code.
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ANSI colors (kept subtle; degrade gracefully in any terminal)
DIM = "\033[2m"
RESET = "\033[0m"
ACCENT = "\033[38;5;179m"   # warm amber
BOLD = "\033[1m"
GREEN = "\033[38;5;71m"
SEP = f"{DIM} · {RESET}"


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return

    try:
        import ccstats
    except Exception:
        return

    gap = float(os.environ.get("CC_TIME_GAP", str(ccstats.DEFAULT_GAP_MIN)))
    show_cost = os.environ.get("CC_TIME_SHOW_COST", "1") != "0"
    icon = os.environ.get("CC_TIME_ICON", "⏱")

    cache_dir = ccstats.DEFAULT_CACHE_DIR
    if len(sys.argv) > 1 and sys.argv[1].strip() and "${" not in sys.argv[1]:
        cache_dir = sys.argv[1]

    transcript = payload.get("transcript_path") or ""
    project_dir = os.path.dirname(transcript) if transcript else ""
    if not project_dir or not os.path.isdir(project_dir):
        return

    try:
        agg = ccstats.analyze_project(project_dir, gap, cache_dir)
    except Exception:
        return

    name = agg.get("project") or "project"

    parts = [f"{ACCENT}{icon} {BOLD}{name}{RESET}"]

    total = agg.get("active_seconds", 0)
    if total > 0:
        parts.append(f"{ccstats.fmt_dur(total)}{DIM} total{RESET}")

    today = agg.get("today_seconds", 0)
    if today > 0:
        parts.append(f"{GREEN}{ccstats.fmt_dur(today)}{RESET}{DIM} today{RESET}")

    # session = ACTIVE time in the current transcript, same gap rule as "total".
    # Not the payload's cost.total_duration_ms: that is wall-clock since the
    # session was first opened and keeps running while the window stays open,
    # including while the computer sleeps (a session left open for two weeks
    # showed "409h session" against 36h of actual work).
    session_secs = 0
    try:
        fd = ccstats.analyze_file(transcript, gap, cache_dir)
        if fd:
            session_secs = sum(b["seconds"] for b in fd.get("blocks") or [])
    except Exception:
        session_secs = 0
    if session_secs > 0:
        parts.append(f"{ccstats.fmt_dur(session_secs)}{DIM} session{RESET}")

    if show_cost:
        toks = agg.get("total_tokens", 0)
        cost_usd = agg.get("cost_usd", 0.0)
        seg = f"{DIM}{ccstats.fmt_tokens(toks)} tok{RESET}"
        if cost_usd >= 0.005:
            seg += f"{DIM} ~${_fmt_money(cost_usd)}{RESET}"
        parts.append(seg)

    sys.stdout.write(SEP.join(parts))


def _fmt_money(v):
    if v >= 1000:
        return f"{v/1000:.1f}k"
    if v >= 100:
        return f"{v:.0f}"
    return f"{v:.2f}"


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
