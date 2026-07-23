---
description: Show a per-project time & token report (table + HTML)
---

Run the Time Tracker analyzer and show the results.

1. Run this command and show the user the full table output verbatim in a code block:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ccstats.py" --table --html
   ```

   This prints a per-project summary (active time, today, sessions, tokens, estimated cost)
   and also writes a self-contained HTML report. Report the HTML file path it prints.

2. Briefly summarize the top 3 projects by active time.

3. Remind the user that the estimated cost assumes pay-as-you-go API pricing; on a
   Max/Pro subscription there is no per-token charge — it is only a usage-intensity indicator.

If `$ARGUMENTS` contains a project name, pass it through as `--project "$ARGUMENTS"` to filter.
