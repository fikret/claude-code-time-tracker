---
description: Enable the Time Tracker status line (adds it to your settings.json)
---

Enable the per-project time & token status line.

Claude Code plugins can't register the main status line directly, so this copies the
tracker scripts to a stable location (`~/.claude/time-tracker/`) and adds a `statusLine`
entry to the user's `~/.claude/settings.json`.

Run exactly this and show the user its output:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install-statusline.py"
```

Then tell the user to **restart Claude Code (or start a new session)** for the status line
to appear at the bottom. To disable it later, they can run
`python3 ~/.claude/time-tracker/statusline.py` is the script; removal is
`/time-tracker:setup` with `--uninstall` via
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install-statusline.py" --uninstall`.
