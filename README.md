# Claude Code Time Tracker

See **how much time and how many tokens** you spend on each project — right in your
Claude Code status line.

```
⏱ elabRandevu · 12h15m total · 59m today · 23m session · 1.4B tok ~$2.9k
```

- **total** — active work time on this project, all-time
- **today** — active work time today
- **session** — wall-clock time in the current session
- **tokens / ~cost** — total tokens and an estimated API cost for this project

Everything is computed from Claude Code's own session transcripts
(`~/.claude/projects/**/*.jsonl`), so it works **retroactively the moment you install it** —
no tracking daemon, no setup, no data leaves your machine.

## How "active time" is measured

Transcripts timestamp every message. The tracker sorts them and sums the gaps between
consecutive messages that are **shorter than a break threshold** (default **5 minutes**).
Long idle gaps are treated as breaks and excluded, so you get realistic hands-on time
rather than raw wall-clock.

## Install

```
/plugin marketplace add fikret/claude-code-time-tracker
/plugin install time-tracker@time-tracker
/time-tracker:setup          # enable the status line
```

Then **restart Claude Code** (or start a new session) — the status line appears at the bottom.

> Requires `python3` on your PATH (preinstalled on macOS and most Linux).

### Why the extra `setup` step?

Claude Code plugins can register commands, hooks, agents and MCP servers, but **not the
main status line** — only the user's own `~/.claude/settings.json` can. So `/time-tracker:setup`
copies the tracker to a stable path (`~/.claude/time-tracker/`) and adds the `statusLine`
entry for you. To disable it again, run
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install-statusline.py" --uninstall`.

Prefer to do it by hand? Add this to `~/.claude/settings.json`:

```json
"statusLine": {
  "type": "command",
  "command": "python3 ~/.claude/time-tracker/statusline.py ~/.claude/.cc-time-cache"
}
```
(after copying `scripts/statusline.py` and `scripts/ccstats.py` into `~/.claude/time-tracker/`).

## Command

```
/time-tracker:report            # full per-project table + HTML report
/time-tracker:report elab       # filter by project name
```

The HTML report shows a bar chart per project and, when you click a project, a breakdown
of every individual work session (date, time range, duration).

## Configuration

Set these environment variables (e.g. in your shell profile) to tune the status line:

| Variable | Default | Effect |
|---|---|---|
| `CC_TIME_GAP` | `5` | Break threshold in minutes |
| `CC_TIME_SHOW_COST` | `1` | Set `0` to hide the token/cost segment |
| `CC_TIME_ICON` | `⏱` | Leading icon |

## A note on cost

The `~$` figure is an **estimate** based on pay-as-you-go API pricing (editable in
`scripts/ccstats.py`). On a Max/Pro subscription you pay **no per-token fee** — treat it as
a usage-intensity indicator, not a bill. Token counts themselves are exact; the bulk is
usually cache-read (the cheapest tier), which is why the totals look large.

## How it stays fast

The status line runs often, so each transcript file is analyzed once and cached by
`(mtime, size)` under the plugin's data directory. Only the file that changed (your current
session) is re-parsed; everything else is served from cache.

## License

MIT
