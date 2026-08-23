#!/usr/bin/env python3
"""
claude_code_token_audit.py - Where are my Claude Code tokens going?

*** CLAUDE CODE ONLY ***
This script is specific to Anthropic's Claude Code (CLI / desktop app / IDE
extensions). It reads the local session transcripts Claude Code writes under
~/.claude/projects. It does NOT work for Codex, Copilot, Cursor, Gemini CLI,
the claude.ai web app, or direct Anthropic API usage - none of those write
this transcript format. Standalone: Python 3.8+, no third-party packages.

Parses every transcript under ~/.claude/projects (main sessions, Agent-tool
subagents, and Workflow agents), de-duplicates streamed partial usage records,
and reports token usage + estimated API-equivalent cost broken down by
model, reasoning effort, thread type (main / subagent / workflow), subagent
type, skill, session, project and day.

Examples
  python claude_code_token_audit.py                       # everything, default tables
  python claude_code_token_audit.py --since 2026-08-01    # date range
  python claude_code_token_audit.py --project watershed   # filter project dir by substring
  python claude_code_token_audit.py --by model,effort,thread,agent
  python claude_code_token_audit.py --by model --by effort --top 30
  python claude_code_token_audit.py --sessions 25         # top-N sessions table size
  python claude_code_token_audit.py --csv out.csv         # one row per deduped API call
  python claude_code_token_audit.py --json out.json       # same, as JSON
  python claude_code_token_audit.py --raw                 # exact numbers, not 1.2M style

Caveats
  * The transcript format is internal to Claude Code and officially "changes
    between versions". Field names below were verified on Claude Code 2.1.2xx
    (Aug 2026). If a future release renames fields, the script degrades to
    "?" buckets rather than crashing, but re-verify before trusting numbers.
  * Claude Code deletes transcripts older than `cleanupPeriodDays` (default 30)
    from settings.json. Raise it or export with --csv periodically to keep history.
  * Cost is an API-list-price estimate (PRICING table). On a Claude subscription
    it is a proxy for rate-limit consumption, not a bill. Update PRICING when
    models or prices change.

Facts about the data (verified against real transcripts, Claude Code 2.1.2xx):
  * One API response is written as SEVERAL jsonl lines (one per content block:
    thinking / text / each tool_use).  All lines carry message.id and a usage
    object; output_tokens grows across the lines.  => group by message.id and
    keep the line with the largest output_tokens or you over-count ~2x.
  * Subagent transcripts live at  <project>/<session>/subagents/agent-*.jsonl
    and Workflow agents at        <project>/<session>/subagents/workflows/wf_*/agent-*.jsonl
  * Assistant lines carry: message.model, message.usage{input_tokens,
    cache_creation_input_tokens, cache_read_input_tokens, output_tokens,
    output_tokens_details.thinking_tokens, cache_creation.ephemeral_{5m,1h}_input_tokens,
    speed}, top-level "effort", "agentId" (subagents only), "attributionAgent",
    "attributionSkill", "sessionId", "timestamp", "cwd".
"""
import argparse, csv, glob, json, os, sys
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
from collections import defaultdict

# ---- pricing: USD per 1M tokens (input, output). Prefix-matched on model id.
PRICING = {
    "claude-fable-5":   (10.0, 50.0),
    "claude-mythos-5":  (10.0, 50.0),
    "claude-opus-5":    (5.0, 25.0),
    "claude-opus-4-8":  (5.0, 25.0),
    "claude-opus-4-7":  (5.0, 25.0),
    "claude-opus-4-6":  (5.0, 25.0),
    "claude-opus-4-5":  (5.0, 25.0),
    "claude-sonnet-5":  (3.0, 15.0),
    "claude-sonnet-4-6":(3.0, 15.0),
    "claude-sonnet-4-5":(3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
FAST_MODE_PRICING = {"claude-opus-5": (10.0, 50.0), "claude-opus-4-8": (10.0, 50.0)}
CACHE_READ_MULT, CACHE_5M_MULT, CACHE_1H_MULT = 0.10, 1.25, 2.00

def price_for(model, speed):
    if not model or model.startswith("<"):
        return (0.0, 0.0)
    table = FAST_MODE_PRICING if speed == "fast" else PRICING
    for k, v in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if model.startswith(k):
            return v
    for k, v in sorted(PRICING.items(), key=lambda kv: -len(kv[0])):
        if model.startswith(k):
            return v
    return (0.0, 0.0)

def est_cost(rec):
    pin, pout = price_for(rec["model"], rec["speed"])
    c5, c1 = rec["cache_5m"], rec["cache_1h"]
    if c5 + c1 == 0 and rec["cache_write"] > 0:   # no ttl breakdown recorded
        c5 = rec["cache_write"]
    return (rec["input"] * pin
            + c5 * pin * CACHE_5M_MULT
            + c1 * pin * CACHE_1H_MULT
            + rec["cache_read"] * pin * CACHE_READ_MULT
            + rec["output"] * pout) / 1_000_000

# ---- parsing
def iter_files(root):
    for f in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        yield f.replace("\\", "/")

def classify(path, root):
    rel = os.path.relpath(path, root).replace("\\", "/")
    parts = rel.split("/")
    project = parts[0]
    if "/subagents/workflows/" in rel:
        return project, parts[1], "workflow"
    if "/subagents/" in rel:
        return project, parts[1], "subagent"
    return project, os.path.splitext(parts[-1])[0], "main"

def load(root, since, until, project_filter, session_filter):
    best = {}          # message.id -> record (max output_tokens wins)
    titles = {}        # sessionId -> title
    for path in iter_files(root):
        project, session, thread = classify(path, root)
        if project_filter and project_filter.lower() not in project.lower():
            continue
        if session_filter and not session.startswith(session_filter):
            continue
        try:
            fh = open(path, encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"type":"assistant"' not in line and '"type": "assistant"' not in line:
                    if thread == "main" and ('"custom-title"' in line or '"ai-title"' in line):
                        try:
                            e = json.loads(line)
                            t = e.get("customTitle") or e.get("aiTitle")
                            if t: titles[e.get("sessionId", session)] = t
                        except Exception:
                            pass
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("type") != "assistant":
                    continue
                msg = e.get("message") or {}
                u = msg.get("usage") or {}
                if not u:
                    continue
                ts = e.get("timestamp", "")
                day = ts[:10]
                if since and day < since:  continue
                if until and day > until:  continue
                mid = msg.get("id") or e.get("requestId") or e.get("uuid")
                cc = u.get("cache_creation") or {}
                od = u.get("output_tokens_details") or {}
                rec = {
                    "msg_id": mid,
                    "timestamp": ts, "day": day,
                    "project": project, "session": session, "thread": thread,
                    "agent_id": e.get("agentId") or "",
                    "agent_type": e.get("attributionAgent") or ("" if thread == "main" else "?"),
                    "skill": e.get("attributionSkill") or "",
                    "model": msg.get("model") or "?",
                    "effort": e.get("effort") or "?",
                    "speed": u.get("speed") or "standard",
                    "input": u.get("input_tokens") or 0,
                    "cache_write": u.get("cache_creation_input_tokens") or 0,
                    "cache_5m": cc.get("ephemeral_5m_input_tokens") or 0,
                    "cache_1h": cc.get("ephemeral_1h_input_tokens") or 0,
                    "cache_read": u.get("cache_read_input_tokens") or 0,
                    "output": u.get("output_tokens") or 0,
                    "thinking": od.get("thinking_tokens") or 0,
                    "version": e.get("version", ""),
                    "cwd": e.get("cwd", ""),
                }
                prev = best.get(mid)
                if prev is None or rec["output"] >= prev["output"]:
                    best[mid] = rec
    recs = list(best.values())
    for r in recs:
        r["context"] = r["input"] + r["cache_write"] + r["cache_read"]   # tokens the model read
        r["total"] = r["context"] + r["output"]
        r["cost"] = est_cost(r)
    return recs, titles

# ---- aggregation / display
COLS = ["calls", "input", "cache_write", "cache_read", "output", "thinking", "total", "cost"]

def agg(recs, keyfn):
    out = defaultdict(lambda: {c: 0 for c in COLS})
    for r in recs:
        k = keyfn(r)
        a = out[k]
        a["calls"] += 1
        for c in ("input", "cache_write", "cache_read", "output", "thinking", "total", "cost"):
            a[c] += r[c]
    return out

def fmt_n(n, raw):
    if raw: return f"{n:,}"
    if n >= 1e9: return f"{n/1e9:.2f}B"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    if n >= 1e3: return f"{n/1e3:.0f}K"
    return str(n)

def table(title, rows, raw, top=None, note=None, sort_key=None, reverse=False):
    tot = sum(v["total"] for v in rows.values()) or 1
    costtot = sum(v["cost"] for v in rows.values()) or 1
    if sort_key is None:
        sort_key = lambda kv: -kv[1]["cost"] if kv[1]["cost"] else -kv[1]["total"]
    items = sorted(rows.items(), key=sort_key, reverse=reverse)
    shown = items[:top] if top else items
    keyw = max([len(str(k)) for k, _ in shown] + [8])
    keyw = min(keyw, 60)
    print(f"\n== {title} ==")
    if note: print(f"   {note}")
    hdr = f"{'':<{keyw}}  {'calls':>6} {'input':>8} {'cache_w':>8} {'cache_r':>8} {'output':>8} {'think':>7} {'total':>8} {'%tok':>5} {'est$':>9} {'%$':>5}"
    print(hdr); print("-" * len(hdr))
    for k, v in shown:
        ks = str(k)
        if len(ks) > 60: ks = ks[:57] + "..."
        print(f"{ks:<{keyw}}  {v['calls']:>6} {fmt_n(v['input'],raw):>8} {fmt_n(v['cache_write'],raw):>8} "
              f"{fmt_n(v['cache_read'],raw):>8} {fmt_n(v['output'],raw):>8} {fmt_n(v['thinking'],raw):>7} "
              f"{fmt_n(v['total'],raw):>8} {100*v['total']/tot:>4.0f}% {v['cost']:>9.2f} {100*v['cost']/costtot:>4.0f}%")
    if top and len(items) > top:
        rest = items[top:]
        print(f"{'(+%d more)' % len(rest):<{keyw}}  {sum(v['calls'] for _,v in rest):>6} {'':>8} {'':>8} {'':>8} {'':>8} {'':>7} "
              f"{fmt_n(sum(v['total'] for _,v in rest),raw):>8} {'':>5} {sum(v['cost'] for _,v in rest):>9.2f}")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    ap.add_argument("--since", help="YYYY-MM-DD (UTC, inclusive)")
    ap.add_argument("--until", help="YYYY-MM-DD (UTC, inclusive)")
    ap.add_argument("--project", help="substring filter on the encoded project dir name")
    ap.add_argument("--session", help="session id prefix filter")
    ap.add_argument("--by", action="append", default=[],
                    help="comma list of: thread,model,effort,model_effort,model_thread,effort_thread,agent,skill,session,project,day,version,speed,all")
    ap.add_argument("--top", type=int, default=20, help="rows per table (default 20)")
    ap.add_argument("--sessions", type=int, default=15, help="rows in the sessions table")
    ap.add_argument("--raw", action="store_true", help="exact numbers instead of K/M")
    ap.add_argument("--no-cost", action="store_true")
    ap.add_argument("--csv"); ap.add_argument("--json")
    a = ap.parse_args()

    recs, titles = load(a.root, a.since, a.until, a.project, a.session)
    if not recs:
        print("no assistant usage records found"); return
    if a.no_cost:
        for r in recs: r["cost"] = 0.0

    dims = set()
    for b in a.by: dims.update(x.strip() for x in b.split(",") if x.strip())
    if not dims: dims = {"thread", "model", "effort", "model_effort", "model_thread", "agent", "skill", "session", "project", "day"}
    if "all" in dims: dims = {"thread","model","effort","model_effort","model_thread","effort_thread","agent","skill","session","project","day","version","speed"}

    days = sorted({r["day"] for r in recs if r["day"]})
    t = agg(recs, lambda r: "ALL")["ALL"]
    print(f"Claude Code token audit - {len(recs):,} API calls, {days[0]} -> {days[-1]}"
          + (f", project~'{a.project}'" if a.project else ""))
    print(f"  tokens read by model (input+cache_write+cache_read): {t['input']+t['cache_write']+t['cache_read']:,}")
    print(f"    uncached input {t['input']:,} | cache writes {t['cache_write']:,} | cache reads {t['cache_read']:,}")
    print(f"  output tokens {t['output']:,} (of which thinking {t['thinking']:,} - only recorded on some calls)")
    print(f"  est. API-equivalent cost ${t['cost']:,.2f}  (cache read 0.1x, 5m write 1.25x, 1h write 2x; $0 for unknown models)")
    print("  NOTE: 'est$' is what this usage would cost at API list price; on a Claude subscription it is a proxy for rate-limit consumption, not a bill.")

    KEYS = {
        "thread":        lambda r: r["thread"],
        "model":         lambda r: r["model"],
        "effort":        lambda r: r["effort"],
        "model_effort":  lambda r: f"{r['model']} @ {r['effort']}",
        "model_thread":  lambda r: f"{r['model']} / {r['thread']}",
        "effort_thread": lambda r: f"{r['effort']} / {r['thread']}",
        "agent":         lambda r: r["agent_type"] if r["thread"] != "main" else "(main thread)",
        "skill":         lambda r: r["skill"] or "(no skill)",
        "session":       lambda r: f"{r['session'][:8]} {titles.get(r['session'], '')[:40]} [{r['project'][-28:]}]",
        "project":       lambda r: r["project"],
        "day":           lambda r: r["day"],
        "version":       lambda r: r["version"],
        "speed":         lambda r: r["speed"],
    }
    TITLES = {
        "thread": "by thread type (main conversation vs Agent-tool subagents vs Workflow agents)",
        "model": "by model", "effort": "by reasoning effort", "model_effort": "by model x effort",
        "model_thread": "by model x thread", "effort_thread": "by effort x thread",
        "agent": "by subagent type (attributionAgent)", "skill": "by skill (attributionSkill)",
        "project": "by project directory", "session": "top sessions", "version": "by Claude Code version", "speed": "by speed (fast mode)",
    }
    order = ["thread","model","effort","model_effort","model_thread","effort_thread","agent","skill","project","session","day","version","speed"]
    for name in order:
        if name not in dims: continue
        if name == "day":
            rows = agg(recs, KEYS["day"])
            recent = dict(sorted(rows.items(), key=lambda kv: kv[0], reverse=True)[:a.top])
            table("by day (most recent first)", recent, a.raw, sort_key=lambda kv: kv[0], reverse=True, top=None)

        else:
            table(TITLES[name], agg(recs, KEYS[name]), a.raw, top=(a.sessions if name == "session" else a.top))

    if a.csv or a.json:
        fields = ["timestamp","day","project","session","thread","agent_id","agent_type","skill","model","effort","speed",
                  "input","cache_write","cache_5m","cache_1h","cache_read","output","thinking","context","total","cost","version","msg_id"]
        recs.sort(key=lambda r: r["timestamp"])
        if a.csv:
            with open(a.csv, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(recs)
            print(f"\nwrote {len(recs):,} rows -> {a.csv}")
        if a.json:
            with open(a.json, "w", encoding="utf-8") as fh:
                json.dump([{k: r[k] for k in fields} for r in recs], fh)
            print(f"wrote {len(recs):,} rows -> {a.json}")

if __name__ == "__main__":
    main()
