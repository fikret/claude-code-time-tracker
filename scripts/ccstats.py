#!/usr/bin/env python3
"""
ccstats.py — shared library + CLI for the Claude Code Time Tracker plugin.

Reads Claude Code session transcripts (~/.claude/projects/<encoded>/*.jsonl),
derives per-project "active work time" via gap analysis, and tallies token
usage / estimated cost. Results are cached per transcript file (keyed by
mtime + size) so repeated calls (e.g. from the status line) stay fast.

CLI:
  python3 ccstats.py --table            # summary table for all projects
  python3 ccstats.py --html [OUT.html]  # write a self-contained HTML report
  python3 ccstats.py --project NAME     # filter by project name (substring)
  python3 ccstats.py --gap 5            # break threshold in minutes
"""

import os
import sys
import json
import glob
import hashlib
import argparse
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
DEFAULT_CACHE_DIR = os.path.join(HOME, ".claude", ".cc-time-cache")

# --- configurable via env ---------------------------------------------------
DEFAULT_GAP_MIN = float(os.environ.get("CC_TIME_GAP", "5"))  # break threshold (min)

# Estimated cost pricing (USD per 1M tokens). EDITABLE.
# Note: on a Max/Pro subscription you pay no per-token fee; this only estimates
# what the same usage would roughly cost on the pay-as-you-go API.
MODEL_PRICING = {
    "opus":   {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "sonnet": {"input": 3.0,  "output": 15.0, "cache_write": 3.75,  "cache_read": 0.30},
    "haiku":  {"input": 0.80, "output": 4.0,  "cache_write": 1.00,  "cache_read": 0.08},
}

CACHE_VERSION = 2  # bump when per-file cache schema changes


def price_for_model(model):
    m = (model or "").lower()
    if "opus" in m:
        return MODEL_PRICING["opus"]
    if "sonnet" in m:
        return MODEL_PRICING["sonnet"]
    if "haiku" in m:
        return MODEL_PRICING["haiku"]
    return None  # <synthetic> etc. -> no cost


def parse_ts(s):
    """ISO 8601 (Z or offset) -> aware datetime in UTC, or None."""
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def work_blocks(stamps, gap_min):
    """Split sorted UTC timestamps into work sessions ("blocks").
    A gap larger than gap_min minutes starts a new block."""
    if not stamps:
        return []
    stamps = sorted(stamps)
    gap = gap_min * 60
    blocks = []
    b_start = b_prev = stamps[0]
    b_msgs = 1
    for t in stamps[1:]:
        delta = (t - b_prev).total_seconds()
        if 0 <= delta <= gap:
            b_prev = t
            b_msgs += 1
        else:
            blocks.append((b_start, b_prev, b_msgs))
            b_start = b_prev = t
            b_msgs = 1
    blocks.append((b_start, b_prev, b_msgs))
    return blocks


# --- per-file analysis with caching -----------------------------------------

def _cache_path(cache_dir, transcript_path):
    key = hashlib.sha1(transcript_path.encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, key + ".json")


def analyze_file(path, gap_min, cache_dir):
    """Analyze a single transcript file; cache result by (mtime, size, gap).
    Returns dict: {blocks, tokens, cost, msgs, cwd, first, last} where blocks
    hold LOCAL-time ISO strings so 'today' can be computed at read time."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    sig = {"v": CACHE_VERSION, "mtime": st.st_mtime, "size": st.st_size, "gap": gap_min}

    cp = _cache_path(cache_dir, path)
    try:
        with open(cp, "r", encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("sig") == sig:
            return cached["data"]
    except Exception:
        pass

    stamps = []
    msgs = 0
    cwd = None
    tok = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    cost = 0.0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if cwd is None:
                    c = d.get("cwd")
                    if isinstance(c, str) and c:
                        cwd = c
                dt = parse_ts(d.get("timestamp"))
                if dt:
                    stamps.append(dt)
                    msgs += 1
                msg = d.get("message") or {}
                u = msg.get("usage")
                if isinstance(u, dict):
                    i = u.get("input_tokens", 0) or 0
                    o = u.get("output_tokens", 0) or 0
                    cw = u.get("cache_creation_input_tokens", 0) or 0
                    cr = u.get("cache_read_input_tokens", 0) or 0
                    tok["input"] += i
                    tok["output"] += o
                    tok["cache_write"] += cw
                    tok["cache_read"] += cr
                    p = price_for_model(msg.get("model"))
                    if p:
                        cost += (i * p["input"] + o * p["output"]
                                 + cw * p["cache_write"] + cr * p["cache_read"]) / 1_000_000
    except Exception:
        return None

    blocks = [{
        "start": bs.astimezone().isoformat(),
        "end": be.astimezone().isoformat(),
        "seconds": round((be - bs).total_seconds(), 1),
        "messages": m,
    } for (bs, be, m) in work_blocks(stamps, gap_min)]

    data = {
        "blocks": blocks,
        "tokens": tok,
        "cost": round(cost, 4),
        "msgs": msgs,
        "cwd": cwd,
        "first": min(stamps).astimezone().isoformat() if stamps else None,
        "last": max(stamps).astimezone().isoformat() if stamps else None,
    }
    try:
        os.makedirs(cache_dir, exist_ok=True)
        tmp = cp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"sig": sig, "data": data}, fh)
        os.replace(tmp, cp)
    except Exception:
        pass
    return data


def _decode_project_name(project_dir):
    """Best-effort project name from the encoded dir when no cwd is available."""
    base = os.path.basename(project_dir)
    # encoded dir = absolute path with '/' replaced by '-'
    cand = "/" + base.lstrip("-").replace("-", "/")
    if os.path.isdir(cand):
        return os.path.basename(cand)
    parts = base.strip("-").split("-")
    return parts[-1] if parts else base


def analyze_project(project_dir, gap_min=DEFAULT_GAP_MIN, cache_dir=DEFAULT_CACHE_DIR):
    """Aggregate all transcripts in a project dir. Returns an aggregate dict."""
    files = glob.glob(os.path.join(project_dir, "*.jsonl"))
    blocks = []
    tok = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    cost = 0.0
    msgs = 0
    sessions = 0
    cwd = None
    firsts, lasts = [], []
    for path in files:
        fd = analyze_file(path, gap_min, cache_dir)
        if not fd or not fd.get("blocks"):
            continue
        sessions += 1
        blocks.extend(fd["blocks"])
        for k in tok:
            tok[k] += fd["tokens"].get(k, 0)
        cost += fd["cost"]
        msgs += fd["msgs"]
        cwd = cwd or fd.get("cwd")
        if fd.get("first"):
            firsts.append(fd["first"])
        if fd.get("last"):
            lasts.append(fd["last"])

    blocks.sort(key=lambda b: b["start"], reverse=True)
    active = sum(b["seconds"] for b in blocks)
    total_tokens = tok["input"] + tok["output"] + tok["cache_write"] + tok["cache_read"]

    today = datetime.now().astimezone().date().isoformat()
    today_secs = sum(b["seconds"] for b in blocks if b["start"][:10] == today)

    name = os.path.basename(cwd) if cwd else _decode_project_name(project_dir)
    return {
        "project": name,
        "path": cwd,
        "active_seconds": round(active, 1),
        "today_seconds": round(today_secs, 1),
        "sessions": sessions,
        "messages": msgs,
        "tokens": tok,
        "total_tokens": total_tokens,
        "cost_usd": round(cost, 2),
        "blocks": blocks,
        "first": min(firsts) if firsts else None,
        "last": max(lasts) if lasts else None,
    }


def all_projects(gap_min=DEFAULT_GAP_MIN, cache_dir=DEFAULT_CACHE_DIR, name_filter=None):
    rows = []
    for entry in sorted(glob.glob(os.path.join(PROJECTS_DIR, "*"))):
        if not os.path.isdir(entry):
            continue
        agg = analyze_project(entry, gap_min, cache_dir)
        if not agg["blocks"]:
            continue
        if name_filter and name_filter.lower() not in agg["project"].lower():
            continue
        rows.append(agg)
    rows.sort(key=lambda r: r["active_seconds"], reverse=True)
    return rows


# --- formatting -------------------------------------------------------------

def fmt_tokens(n):
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def fmt_dur(secs):
    """Compact duration: 12h5m / 45m / <1m."""
    secs = int(round(secs))
    h = secs // 3600
    m = (secs % 3600) // 60
    if h:
        return f"{h}h{m}m"
    if m:
        return f"{m}m"
    return "<1m"


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def fmt_block_html(b):
    s = datetime.fromisoformat(b["start"])
    e = datetime.fromisoformat(b["end"])

    def dm(dt):
        return f"{dt.day} {_MONTHS[dt.month - 1]}"

    def hm(dt):
        return f"{dt.hour:02d}:{dt.minute:02d}"

    if s.date() == e.date():
        when = f"{dm(s)} {_DAYS[s.weekday()]} · {hm(s)}–{hm(e)}"
    else:
        when = f"{dm(s)} {hm(s)} → {dm(e)} {hm(e)}"
    dur = fmt_dur(b["seconds"])
    return (f'<div class="blk"><span class="bwhen">{when}</span>'
            f'<span class="bmeta">{dur} · {b["messages"]} msg</span></div>')


# --- CLI outputs ------------------------------------------------------------

def print_table(rows):
    total_active = sum(r["active_seconds"] for r in rows)
    total_tokens = sum(r["total_tokens"] for r in rows)
    total_cost = sum(r["cost_usd"] for r in rows)
    print()
    print(f"{'PROJECT':<26}{'ACTIVE':>10}{'TODAY':>9}{'SESS':>6}{'TOKENS':>10}{'~COST':>11}   RANGE")
    print("-" * 100)
    for r in rows:
        rng = f"{(r['first'] or '')[:10]}→{(r['last'] or '')[:10]}"
        print(f"{r['project']:<26}{fmt_dur(r['active_seconds']):>10}"
              f"{fmt_dur(r['today_seconds']):>9}{r['sessions']:>6}"
              f"{fmt_tokens(r['total_tokens']):>10}"
              f"{'$'+format(r['cost_usd'], '.2f'):>11}   {rng}")
    print("-" * 100)
    print(f"{'TOTAL':<26}{fmt_dur(total_active):>10}{'':>9}"
          f"{sum(r['sessions'] for r in rows):>6}{fmt_tokens(total_tokens):>10}"
          f"{'$'+format(total_cost, '.2f'):>11}")
    print()


def build_report_data(rows, gap_min):
    return {
        "gap_minutes": gap_min,
        "total_active_seconds": round(sum(r["active_seconds"] for r in rows), 1),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 2),
        "projects": rows,
    }


def render_html(data):
    rows = data["projects"]
    max_secs = max((r["active_seconds"] for r in rows), default=1) or 1
    total_hours = data["total_active_seconds"] / 3600.0
    total_tokens = data.get("total_tokens", 0)
    total_cost = data.get("total_cost_usd", 0.0)

    bars = []
    for i, r in enumerate(rows):
        pct = max(1.5, r["active_seconds"] / max_secs * 100)
        lead = i == 0
        blk = r.get("blocks", [])
        detail_lines = "".join(fmt_block_html(b) for b in blk)
        bars.append(f"""
        <div class="row{' lead' if lead else ''}" onclick="tgl(this,'d{i}')" title="Click for detail">
          <div class="pname"><span class="caret">▸</span>{r['project']}</div>
          <div class="track"><div class="fill" style="width:{pct:.1f}%"></div></div>
          <div class="pval"><span class="dur">{fmt_dur(r['active_seconds'])}</span><span class="sub">{fmt_tokens(r['total_tokens'])} tok · ${r['cost_usd']:.2f}</span></div>
        </div>
        <div class="detail" id="d{i}">
          <div class="dhead">{len(blk)} work sessions · break threshold {int(data['gap_minutes'])} min</div>
          {detail_lines}
        </div>""")

    trows = []
    for r in rows:
        trows.append(f"""<tr>
          <td>{r['project']}</td>
          <td class="num">{fmt_dur(r['active_seconds'])}</td>
          <td class="num">{fmt_dur(r['today_seconds'])}</td>
          <td class="num">{r['sessions']}</td>
          <td class="num">{fmt_tokens(r['total_tokens'])}</td>
          <td class="num accent">${r['cost_usd']:.2f}</td>
          <td class="date">{(r['first'] or '')[:10]} → {(r['last'] or '')[:10]}</td>
        </tr>""")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Code — Time Report</title>
<style>
  :root {{
    --bg:#f6f6f4; --panel:#fdfdfc; --ink:#1b1d21; --muted:#6a7078; --faint:#9aa0a8;
    --line:#e4e3de; --track:#eae9e4; --accent:#c8811f; --accent-soft:#e9b45a;
    --good:#3f8f6b; --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#101216; --panel:#171a1f; --ink:#e9e9e6; --muted:#98a0aa; --faint:#6b7580;
      --line:#262a31; --track:#22262d; --accent:#e0a83c; --accent-soft:#7a5e28; --good:#5fb98d; }}
  }}
  :root[data-theme="dark"] {{ --bg:#101216; --panel:#171a1f; --ink:#e9e9e6; --muted:#98a0aa;
    --faint:#6b7580; --line:#262a31; --track:#22262d; --accent:#e0a83c; --accent-soft:#7a5e28; --good:#5fb98d; }}
  :root[data-theme="light"] {{ --bg:#f6f6f4; --panel:#fdfdfc; --ink:#1b1d21; --muted:#6a7078;
    --faint:#9aa0a8; --line:#e4e3de; --track:#eae9e4; --accent:#c8811f; --accent-soft:#e9b45a; --good:#3f8f6b; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased; }}
  .wrap {{ max-width:880px; margin:0 auto; padding:44px 22px 72px; }}
  .eyebrow {{ font:600 11px/1 var(--mono); letter-spacing:.18em; text-transform:uppercase;
    color:var(--accent); margin:0 0 12px; }}
  h1 {{ font-size:27px; font-weight:680; letter-spacing:-.02em; margin:0 0 6px; text-wrap:balance; }}
  .lede {{ color:var(--muted); margin:0 0 32px; max-width:60ch; }}
  .stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:var(--line);
    border:1px solid var(--line); border-radius:12px; overflow:hidden; margin-bottom:34px; }}
  @media (max-width:560px) {{ .stats {{ grid-template-columns:repeat(2,1fr); }} }}
  .stat {{ background:var(--panel); padding:18px 20px; }}
  .stat .big {{ font:660 28px/1 var(--mono); letter-spacing:-.02em; font-variant-numeric:tabular-nums; }}
  .stat.hi .big {{ color:var(--accent); }}
  .stat .lbl {{ color:var(--muted); font-size:12px; margin-top:7px;
    text-transform:uppercase; letter-spacing:.06em; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:14px;
    padding:24px 26px; margin-bottom:24px; }}
  .panel h2 {{ font:600 12px/1 var(--mono); letter-spacing:.12em; text-transform:uppercase;
    color:var(--muted); margin:0 0 20px; }}
  .row {{ display:grid; grid-template-columns:135px 1fr 150px; align-items:center;
    gap:16px; padding:7px 0; cursor:pointer; border-radius:8px; transition:background .12s; }}
  .row:hover {{ background:color-mix(in srgb,var(--accent) 8%,transparent); }}
  .caret {{ display:inline-block; color:var(--accent); margin-right:6px; font-size:10px;
    transition:transform .18s; }}
  .row.open .caret {{ transform:rotate(90deg); }}
  .pname {{ font-size:13.5px; font-weight:520; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
  .detail {{ display:none; margin:2px 0 10px 20px; padding:8px 0 8px 16px;
    border-left:2px solid var(--track); }}
  .detail.open {{ display:block; }}
  .dhead {{ font:600 10px/1 var(--mono); letter-spacing:.08em; text-transform:uppercase;
    color:var(--faint); margin-bottom:8px; }}
  .blk {{ display:flex; justify-content:space-between; gap:12px; padding:5px 0;
    font-size:12.5px; border-bottom:1px dashed var(--line); }}
  .blk:last-child {{ border-bottom:none; }}
  .bwhen {{ font-family:var(--mono); }}
  .bmeta {{ color:var(--muted); font-family:var(--mono); font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .track {{ background:var(--track); border-radius:3px; height:10px; overflow:hidden; }}
  .fill {{ height:100%; border-radius:3px; background:var(--accent-soft); transition:width .5s ease; }}
  .row.lead .fill {{ background:var(--accent); }}
  .pval {{ text-align:right; }}
  .pval .dur {{ font:600 14px/1 var(--mono); font-variant-numeric:tabular-nums; }}
  .pval .sub {{ display:block; color:var(--faint); font-size:11px; margin-top:4px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); }}
  thead th {{ color:var(--muted); font:600 11px/1 var(--mono); letter-spacing:.08em;
    text-transform:uppercase; border-bottom:1.5px solid var(--line); }}
  tbody tr:last-child td {{ border-bottom:none; }}
  td.num, th.num {{ text-align:right; font-family:var(--mono); font-variant-numeric:tabular-nums; }}
  td.accent {{ color:var(--accent); font-weight:600; }}
  td.date {{ color:var(--muted); font-family:var(--mono); font-size:12px; }}
  .foot {{ color:var(--faint); font-size:12px; line-height:1.6; margin:16px 2px 0; }}
  .scroll {{ overflow-x:auto; }}
</style>
</head>
<body>
<div class="wrap">
  <p class="eyebrow">Claude Code · Telemetry</p>
  <h1>Project Time Report</h1>
  <p class="lede">Active work time derived from session transcripts. Time is the sum of gaps
  shorter than {int(data['gap_minutes'])} minutes between consecutive messages — long breaks are excluded.</p>

  <div class="stats">
    <div class="stat hi"><div class="big">{total_hours:.0f}h</div><div class="lbl">Total active</div></div>
    <div class="stat"><div class="big">{fmt_tokens(total_tokens)}</div><div class="lbl">Total tokens</div></div>
    <div class="stat hi"><div class="big">${total_cost:,.0f}</div><div class="lbl">~ Est. cost</div></div>
    <div class="stat"><div class="big">{len(rows)}</div><div class="lbl">Projects</div></div>
  </div>

  <div class="panel">
    <h2>Active time by project <span style="color:var(--faint);font-weight:400;text-transform:none;letter-spacing:0">— click a row for detail</span></h2>
    {''.join(bars)}
  </div>

  <div class="panel scroll">
    <h2>Details</h2>
    <table>
      <thead><tr>
        <th>Project</th><th class="num">Active</th><th class="num">Today</th><th class="num">Sessions</th>
        <th class="num">Tokens</th><th class="num">~ Cost</th><th>Date range</th>
      </tr></thead>
      <tbody>{''.join(trows)}</tbody>
    </table>
    <p class="foot">Time is an estimate and may be shorter than wall-clock. Token total is
    input + output + cache (write/read); cache-read dominates. <b>Cost</b> is an
    <b>estimate</b> based on API pricing — on a Max/Pro subscription you pay no per-token fee;
    it only shows what the same usage would roughly cost on the API.</p>
  </div>
</div>
<script>
  function tgl(row, id) {{
    var d = document.getElementById(id);
    if (!d) return;
    var open = d.classList.toggle('open');
    row.classList.toggle('open', open);
  }}
</script>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="Claude Code per-project time & token stats")
    ap.add_argument("--table", action="store_true", help="print summary table (default)")
    ap.add_argument("--html", nargs="?", const="", metavar="OUT",
                    help="write HTML report (default: ~/.claude/.cc-time-cache/report.html)")
    ap.add_argument("--project", help="filter by project name (substring)")
    ap.add_argument("--gap", type=float, default=DEFAULT_GAP_MIN, help="break threshold (minutes)")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="cache directory")
    args = ap.parse_args()

    rows = all_projects(args.gap, args.cache_dir, args.project)

    if args.html is not None:
        data = build_report_data(rows, args.gap)
        out = args.html or os.path.join(args.cache_dir, "report.html")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(render_html(data))
        print(f"HTML report -> {out}")
        if not args.table:
            return

    print_table(rows)


if __name__ == "__main__":
    main()
