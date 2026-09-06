#!/usr/bin/env python3
"""context_cost_audit.py - what does every Claude Code request carry, and why?

Reads the transcripts Claude Code writes under ~/.claude/projects and the
config on disk, then reports:

  1. Real per-request token usage (deduplicated API calls, cache split,
     first-request prefix per session, growth, idle-gap cache rewrites).
  2. The exact character size of every prefix component for one session,
     taken from the `attachment` records Claude Code stores next to the
     conversation (system prompt blocks, tool schemas, CLAUDE.md, agent
     listing, skill listing, MCP instructions, deferred tool names, hook
     context), with tokens allocated from the measured first request.
  3. What each later turn adds and keeps (tool results by tool, hook text per
     prompt, skill invocations, assistant output).
  4. The always-on surface on disk (CLAUDE.md files, agent and skill
     descriptions, hooks, MCP servers) so the numbers map to files.

Standard library only. Python 3.10+. Windows, macOS, Linux.

  python context_cost_audit.py                     # last 7 days, newest session anatomy
  python context_cost_audit.py --days 30 --project myrepo
  python context_cost_audit.py --session <session-id-prefix>
  python context_cost_audit.py --repo C:/src/myrepo   # add repo-level CLAUDE.md/agents/skills
  python context_cost_audit.py --json audit.json

Facts about the transcript format this relies on (Claude Code 2.1.2xx):
  * One API response is written as several jsonl lines sharing message.id;
    usage.output_tokens grows across them. Group by id, keep the max.
  * Context size of a request = input_tokens + cache_creation_input_tokens
    + cache_read_input_tokens. cache_read on the FIRST request of a session is
    the cross-session static block (tool schemas + static system prompt) when
    another session ran within the cache TTL; cache_creation is the rest.
  * Lines with type == "attachment" carry what was injected around the user
    turn: prompt_snapshot (systemPrompt blocks and, when present, tools),
    instructions (CLAUDE.md files), agent_listing_delta, skill_listing,
    mcp_instructions_delta, deferred_tools_delta, hook_additional_context.
  * Subagent transcripts live at <project>/<session>/subagents/**/agent-*.jsonl.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"


# ----------------------------------------------------------------------------
# helpers
def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1)))))
    return float(ordered[k])


def mean(values: list[float]) -> float:
    return float(statistics.fmean(values)) if values else 0.0


def median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def text_len(value: Any) -> int:
    """Characters of an attachment payload that may be str, list, or dict."""
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(text_len(v) for v in value)
    if isinstance(value, dict):
        return len(json.dumps(value, ensure_ascii=False))
    return len(str(value))


def content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    total += len(block.get("text", ""))
                else:
                    total += len(json.dumps(block, ensure_ascii=False))
            else:
                total += len(str(block))
        return total
    return 0


def short_project(name: str) -> str:
    if "scratch-workspaces" in name:
        return "scratch-workspace"
    name = re.sub(r"^[A-Za-z]--Users-[^-]+-", "", name)
    return name[:48]


def fmt(n: float) -> str:
    if abs(n) >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if abs(n) >= 10_000:
        return f"{n/1000:.1f}k"
    return f"{n:,.0f}"


# ----------------------------------------------------------------------------
# transcript parsing
class Session:
    def __init__(self, path: str, project: str, kind: str) -> None:
        self.path = path
        self.project = project
        self.kind = kind  # main | subagent
        self.requests: list[dict[str, Any]] = []  # deduped, in order
        self.tool_results: list[tuple[str, int]] = []
        self.hook_context: list[tuple[str, int]] = []  # (event, chars)
        self.skill_invocations: list[int] = []
        self.compactions = 0
        self.attachments: dict[str, list[dict[str, Any]]] = defaultdict(list)


def parse_session(path: str, project: str, kind: str) -> Session:
    s = Session(path, project, kind)
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    tool_names: dict[str, str] = {}
    try:
        fh = open(path, encoding="utf-8", errors="ignore")
    except OSError:
        return s
    with fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get("type")
            if t == "assistant":
                m = e.get("message") or {}
                mid = m.get("id")
                u = m.get("usage") or {}
                if not mid or not isinstance(u, dict):
                    continue
                rec = best.get(mid)
                out = int(u.get("output_tokens") or 0)
                if rec is None:
                    order.append(mid)
                    rec = {
                        "id": mid,
                        "ts": parse_ts(e.get("timestamp")),
                        "model": m.get("model"),
                        "input": 0,
                        "cache_creation": 0,
                        "cache_read": 0,
                        "output": 0,
                        "thinking": 0,
                        "toolsearch": False,
                        "chars": 0,
                    }
                    best[mid] = rec
                if out >= rec["output"]:
                    rec["output"] = out
                    rec["input"] = int(u.get("input_tokens") or 0)
                    rec["cache_creation"] = int(u.get("cache_creation_input_tokens") or 0)
                    rec["cache_read"] = int(u.get("cache_read_input_tokens") or 0)
                    details = u.get("output_tokens_details") or {}
                    rec["thinking"] = int(details.get("thinking_tokens") or 0)
                for block in m.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        tool_names[str(block.get("id"))] = str(block.get("name"))
                        if block.get("name") == "ToolSearch":
                            rec["toolsearch"] = True
                        rec["chars"] += len(json.dumps(block.get("input"), ensure_ascii=False))
                    elif block.get("type") == "text":
                        rec["chars"] += len(block.get("text", ""))
            elif t == "user":
                m = e.get("message") or {}
                content = m.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            name = tool_names.get(str(block.get("tool_use_id")), "?")
                            s.tool_results.append((name, content_chars(block.get("content"))))
                        elif isinstance(block, dict) and block.get("type") == "text" and "<command-name>" in block.get("text", ""):
                            s.skill_invocations.append(len(block.get("text", "")))
                elif isinstance(content, str) and "<command-name>" in content:
                    s.skill_invocations.append(len(content))
            elif t == "attachment":
                a = e.get("attachment") or {}
                at = a.get("type")
                if not at:
                    continue
                s.attachments[at].append(a)
                if at == "hook_additional_context":
                    s.hook_context.append((str(a.get("hookEvent") or a.get("hookName") or "?"), text_len(a.get("content"))))
            elif t == "system" and e.get("subtype") == "compact_boundary":
                s.compactions += 1
    s.requests = [best[mid] for mid in order]
    return s


def discover(root: Path, days: int, project_filter: str | None) -> list[Session]:
    cutoff = datetime.now().timestamp() - days * 86400
    sessions: list[Session] = []
    for path in glob.glob(str(root / "*" / "*.jsonl")):
        if os.path.getmtime(path) < cutoff:
            continue
        project = Path(path).parent.name
        if project_filter and project_filter.lower() not in project.lower():
            continue
        sessions.append(parse_session(path, project, "main"))
        sub_dir = Path(path).with_suffix("")
        for sub in glob.glob(str(sub_dir / "subagents" / "**" / "*.jsonl"), recursive=True):
            sessions.append(parse_session(sub, project, "subagent"))
    return sessions


# ----------------------------------------------------------------------------
# section 1: usage
def usage_section(sessions: list[Session], top: int) -> tuple[list[str], dict[str, Any]]:
    mains = [s for s in sessions if s.kind == "main" and s.requests]
    subs = [s for s in sessions if s.kind == "subagent" and s.requests]
    out: list[str] = []
    data: dict[str, Any] = {}
    if not mains:
        return ["No main-session transcripts with usage found."], data

    first_totals, first_read, first_create = [], [], []
    by_project: dict[str, list[int]] = defaultdict(list)
    contexts, outputs, thinking, growth = [], [], [], []
    cache_read_sum = cache_create_sum = input_sum = 0
    idle_rewrites = 0
    big_rewrites = 0
    after_toolsearch: list[int] = []
    for s in mains:
        first = s.requests[0]
        ft = first["input"] + first["cache_creation"] + first["cache_read"]
        first_totals.append(ft)
        first_read.append(first["cache_read"])
        first_create.append(first["cache_creation"])
        by_project[short_project(s.project)].append(ft)
        prev_ctx = None
        prev_ts = None
        prev_toolsearch = False
        for i, r in enumerate(s.requests):
            ctx = r["input"] + r["cache_creation"] + r["cache_read"]
            contexts.append(ctx)
            outputs.append(r["output"])
            thinking.append(r["thinking"])
            cache_read_sum += r["cache_read"]
            cache_create_sum += r["cache_creation"]
            input_sum += r["input"]
            if prev_ctx is not None:
                growth.append(ctx - prev_ctx)
            if i > 0 and r["cache_creation"] > 20_000:
                big_rewrites += 1
                if prev_ts and r["ts"] and (r["ts"] - prev_ts) > timedelta(minutes=60):
                    idle_rewrites += 1
            if prev_toolsearch:
                after_toolsearch.append(r["cache_creation"])
            prev_ctx, prev_ts, prev_toolsearch = ctx, r["ts"], r["toolsearch"]

    total_ctx = cache_read_sum + cache_create_sum + input_sum
    sub_first = [s.requests[0]["input"] + s.requests[0]["cache_creation"] + s.requests[0]["cache_read"] for s in subs]
    sub_tokens = sum(r["input"] + r["cache_creation"] + r["cache_read"] + r["output"] for s in subs for r in s.requests)
    main_tokens = total_ctx + sum(outputs)
    n_req = len(contexts)

    data.update(
        sessions=len(mains),
        requests=n_req,
        first_prefix_median=median(first_totals),
        first_prefix_min=min(first_totals),
        first_prefix_max=max(first_totals),
        first_cache_read_median=median(first_read),
        first_cache_creation_median=median(first_create),
        context_mean=mean(contexts),
        context_median=median(contexts),
        context_p90=pct(contexts, 90),
        context_max=max(contexts),
        output_mean=mean(outputs),
        output_median=median(outputs),
        thinking_mean=mean(thinking),
        growth_mean=mean(growth),
        requests_per_session=n_req / len(mains),
        cache_read_pct=(100.0 * cache_read_sum / total_ctx) if total_ctx else 0.0,
        big_rewrites=big_rewrites,
        idle_rewrites=idle_rewrites,
        after_toolsearch_median=median(after_toolsearch),
        compactions=sum(s.compactions for s in mains),
        subagent_transcripts=len(subs),
        subagent_first_prefix_median=median(sub_first),
        subagent_share_pct=(100.0 * sub_tokens / (sub_tokens + main_tokens)) if (sub_tokens + main_tokens) else 0.0,
    )

    out.append("## 1. Real usage from transcripts")
    out.append("")
    out.append(f"Main sessions: {len(mains)}   requests: {n_req}   requests/session: {data['requests_per_session']:.0f}   subagent transcripts: {len(subs)}")
    out.append("")
    out.append("| Metric | Value |")
    out.append("|---|---|")
    out.append(f"| First-request prefix, median (min / max) | {fmt(data['first_prefix_median'])} ({fmt(data['first_prefix_min'])} / {fmt(data['first_prefix_max'])}) |")
    out.append(f"| ... of which cache_read / cache_creation (median) | {fmt(data['first_cache_read_median'])} / {fmt(data['first_cache_creation_median'])} |")
    out.append(f"| Per-request context: mean / median / p90 / max | {fmt(data['context_mean'])} / {fmt(data['context_median'])} / {fmt(data['context_p90'])} / {fmt(data['context_max'])} |")
    out.append(f"| Context growth per request (mean) | {fmt(data['growth_mean'])} |")
    out.append(f"| Output tokens: mean / median (thinking mean) | {data['output_mean']:.0f} / {data['output_median']:.0f} ({data['thinking_mean']:.0f}) |")
    out.append(f"| Context served from cache | {data['cache_read_pct']:.1f}% |")
    out.append(f"| Mid-session cache rewrites > 20k tokens (after > 60 min idle) | {big_rewrites} ({idle_rewrites}) |")
    out.append(f"| cache_creation on the request after a ToolSearch load (median) | {fmt(data['after_toolsearch_median'])} |")
    out.append(f"| Compactions | {data['compactions']} |")
    out.append(f"| Subagent first-request prefix (median) / share of all tokens | {fmt(data['subagent_first_prefix_median'])} / {data['subagent_share_pct']:.1f}% |")
    out.append("")
    out.append("First-request prefix by project (median tokens, sessions):")
    out.append("")
    out.append("| Project | Sessions | Median prefix |")
    out.append("|---|---|---|")
    rows = sorted(by_project.items(), key=lambda kv: -len(kv[1]))[:top]
    for name, vals in rows:
        out.append(f"| {name} | {len(vals)} | {fmt(median(vals))} |")
    data["by_project"] = {k: {"sessions": len(v), "median_prefix": median(v)} for k, v in by_project.items()}
    out.append("")
    return out, data


# ----------------------------------------------------------------------------
# section 2: prefix anatomy from one session's attachments
def anatomy_section(session: Session) -> tuple[list[str], dict[str, Any]]:
    out: list[str] = []
    data: dict[str, Any] = {"session": Path(session.path).stem, "project": short_project(session.project)}
    att = session.attachments
    comps: list[tuple[str, int, str]] = []  # (name, chars, block: static|session)

    system_static = system_dynamic = 0
    tools_chars = 0
    tools_count = 0
    for snap in att.get("prompt_snapshot", []):
        sp = snap.get("systemPrompt")
        blocks = sp if isinstance(sp, list) else ([sp] if isinstance(sp, str) else [])
        static, dynamic, seen_boundary = 0, 0, False
        for b in blocks:
            text = b if isinstance(b, str) else json.dumps(b, ensure_ascii=False)
            if DYNAMIC_BOUNDARY in text:
                seen_boundary = True
                continue
            if seen_boundary:
                dynamic += len(text)
            else:
                static += len(text)
        system_static = max(system_static, static)
        system_dynamic = max(system_dynamic, dynamic)
        tools = snap.get("tools")
        if tools:
            if isinstance(tools, str):
                try:
                    tools = json.loads(tools)
                except ValueError:
                    pass
            tchars = text_len(tools)
            if tchars > tools_chars:
                tools_chars = tchars
                tools_count = len(tools) if isinstance(tools, list) else 0
    if tools_chars:
        comps.append((f"Tool schemas ({tools_count} up-front tools)", tools_chars, "static"))
    if system_static:
        comps.append(("System prompt, static part", system_static, "static"))
    if system_dynamic:
        comps.append(("System prompt, dynamic part (harness, desktop, memory, safety...)", system_dynamic, "session"))
    for ins in att.get("instructions", []):
        for f in ins.get("files", []) or []:
            comps.append((f"Instructions: {f.get('type','?')} {Path(str(f.get('path',''))).name}", len(str(f.get("content", ""))), "session"))
    for a in att.get("agent_listing_delta", []):
        if str(a.get("isInitial")).lower() == "true" or a.get("isInitial") is True:
            comps.append((f"Agent listing ({len(a.get('addedTypes') or [])} agents: name + description)", text_len(a.get("addedLines")), "session"))
    for a in att.get("skill_listing", []):
        if str(a.get("isInitial")).lower() == "true" or a.get("isInitial") is True:
            comps.append((f"Skill listing ({a.get('skillCount','?')} skills: name + description)", text_len(a.get("content")), "session"))
    for a in att.get("mcp_instructions_delta", []):
        comps.append((f"MCP server instructions {a.get('addedNames')}", text_len(a.get("addedBlocks")), "session"))
    deferred = att.get("deferred_tools_delta", [])
    if deferred:
        first = deferred[0]
        comps.append((f"Deferred tool names, first turn ({len(first.get('addedNames') or [])} names)", text_len(first.get("addedLines") or first.get("addedNames")), "session"))
        later = sum(text_len(d.get("addedLines") or d.get("addedNames")) for d in deferred[1:])
        if later:
            comps.append((f"Deferred tool names added later ({len(deferred)-1} deltas)", later, "later"))
    hook_first = sum(c for ev, c in session.hook_context if ev.startswith("SessionStart"))
    if hook_first:
        comps.append(("SessionStart hook context", hook_first, "session"))
    small = 0
    for name in ("environment", "session_context", "model", "date", "plan_mode"):
        if att.get(name):
            small += text_len(att[name][0])
    if small:
        comps.append(("Environment, session context, model, date reminders", small, "session"))

    first = session.requests[0] if session.requests else None
    out.append(f"## 2. Prefix anatomy of session {data['session'][:8]} ({data['project']})")
    out.append("")
    if not comps:
        out.append("No prompt_snapshot / listing attachments in this transcript (older Claude Code version?). Character sizes unavailable; use section 4.")
        return out, data
    static_chars = sum(c for _, c, b in comps if b == "static")
    session_chars = sum(c for _, c, b in comps if b == "session")
    static_tokens = session_tokens = None
    if first:
        prefix_tokens = first["input"] + first["cache_creation"] + first["cache_read"]
        data["first_request_tokens"] = prefix_tokens
        if first["cache_read"] > 0 and static_chars:
            static_tokens, session_tokens = first["cache_read"], first["cache_creation"] + first["input"]
            note = "cache_read on the first request is taken as the static block (tool schemas + static system prompt); cache_creation as the session block."
        else:
            ratio = prefix_tokens / max(1, static_chars + session_chars)
            static_tokens, session_tokens = static_chars * ratio, session_chars * ratio
            note = "Cold cache on the first request: tokens allocated to both blocks by character share."
        out.append(f"Measured first request: {fmt(prefix_tokens)} tokens (cache_read {fmt(first['cache_read'])}, cache_creation {fmt(first['cache_creation'])}). {note}")
        out.append("")
    out.append("| Component | Chars | Block | Allocated tokens |")
    out.append("|---|---|---|---|")
    rows = []
    for name, chars, block in sorted(comps, key=lambda c: -c[1]):
        tok = None
        if block == "static" and static_tokens and static_chars:
            tok = static_tokens * chars / static_chars
        elif block == "session" and session_tokens and session_chars:
            tok = session_tokens * chars / session_chars
        rows.append({"component": name, "chars": chars, "block": block, "tokens": tok})
        out.append(f"| {name} | {chars:,} | {block} | {fmt(tok) if tok else 'n/a'} |")
    out.append(f"| **Total** | {static_chars + session_chars:,} | | {fmt((static_tokens or 0) + (session_tokens or 0))} |")
    out.append("")
    if static_tokens and static_chars:
        out.append(f"Effective chars/token: static block {static_chars / static_tokens:.2f}, session block {session_chars / max(1, session_tokens):.2f}. Allocation within a block is proportional to characters, so per-row tokens are estimates; block totals are measured.")
    out.append("")
    data["components"] = rows
    data["static_chars"], data["session_chars"] = static_chars, session_chars
    return out, data


# ----------------------------------------------------------------------------
# section 3: per-turn additions
def per_turn_section(sessions: list[Session], top: int) -> tuple[list[str], dict[str, Any]]:
    mains = [s for s in sessions if s.kind == "main"]
    out: list[str] = ["## 3. What each later turn adds (and keeps for the rest of the session)", ""]
    data: dict[str, Any] = {}
    by_tool: dict[str, list[int]] = defaultdict(list)
    for s in mains:
        for name, chars in s.tool_results:
            by_tool[name].append(chars)
    out.append("| Tool | Calls | Total chars | Mean | p90 | Max |")
    out.append("|---|---|---|---|---|---|")
    tool_rows = sorted(by_tool.items(), key=lambda kv: -sum(kv[1]))[:top]
    for name, vals in tool_rows:
        out.append(f"| {name[:40]} | {len(vals)} | {sum(vals):,} | {mean(vals):,.0f} | {pct(vals, 90):,.0f} | {max(vals):,} |")
    data["tool_results"] = {n: {"calls": len(v), "total": sum(v), "mean": mean(v), "p90": pct(v, 90), "max": max(v)} for n, v in tool_rows}
    out.append("")
    hooks: dict[str, list[int]] = defaultdict(list)
    for s in mains:
        for ev, chars in s.hook_context:
            hooks[ev].append(chars)
    if hooks:
        out.append("Hook-injected context by event (chars per injection):")
        out.append("")
        out.append("| Event | Injections | Median | Mean | Max |")
        out.append("|---|---|---|---|---|")
        for ev, vals in sorted(hooks.items(), key=lambda kv: -sum(kv[1])):
            out.append(f"| {ev} | {len(vals)} | {median(vals):,.0f} | {mean(vals):,.0f} | {max(vals):,} |")
        data["hooks"] = {ev: {"n": len(v), "median": median(v), "mean": mean(v), "max": max(v)} for ev, v in hooks.items()}
        out.append("")
    skills = [c for s in mains for c in s.skill_invocations]
    if skills:
        out.append(f"Skill / slash-command invocations: {len(skills)}, median {median(skills):,.0f} chars, max {max(skills):,} chars (the skill body stays in context afterwards).")
        data["skill_invocations"] = {"n": len(skills), "median": median(skills), "max": max(skills)}
        out.append("")
    return out, data


# ----------------------------------------------------------------------------
# section 4: on-disk surface
def frontmatter_description(path: Path) -> tuple[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "", ""
    if not text.startswith("---"):
        return "", ""
    end = text.find("\n---", 3)
    if end < 0:
        return "", ""
    fm = text[3:end]
    name = desc = ""
    for line in fm.splitlines():
        if line.startswith("name:"):
            name = line[5:].strip().strip("\"'")
        elif line.startswith("description:"):
            desc = line[12:].strip().strip("\"'")
    return name, desc


def describe_dir(label: str, files: list[Path]) -> tuple[str, dict[str, Any]]:
    items = []
    for f in files:
        name, desc = frontmatter_description(f)
        if name or desc:
            items.append((name or f.stem, len(desc)))
    total = sum(c for _, c in items)
    longest = sorted(items, key=lambda kv: -kv[1])[:5]
    line = f"| {label} | {len(items)} | {total:,} | " + ", ".join(f"{n} ({c})" for n, c in longest) + " |"
    return line, {"count": len(items), "description_chars": total, "longest": longest}


def disk_section(home: Path, repo: Path | None) -> tuple[list[str], dict[str, Any]]:
    out: list[str] = ["## 4. Always-on surface on disk", ""]
    data: dict[str, Any] = {}
    out.append("| Source | Items | Description chars | Longest |")
    out.append("|---|---|---|---|")
    for label, path in (("~/.claude/CLAUDE.md", home / "CLAUDE.md"), ("repo CLAUDE.md", (repo / "CLAUDE.md") if repo else None)):
        if path and path.exists():
            n = len(path.read_text(encoding="utf-8", errors="ignore"))
            out.append(f"| {label} | 1 | {n:,} | whole file is sent |")
            data[label] = n
    line, d = describe_dir("~/.claude/agents", sorted((home / "agents").glob("*.md")))
    out.append(line)
    data["agents"] = d
    line, d = describe_dir("~/.claude/skills", sorted((home / "skills").glob("*/SKILL.md")))
    out.append(line)
    data["skills"] = d
    plugin_skills = sorted((home / "plugins" / "cache").glob("**/SKILL.md"))
    if plugin_skills:
        line, d = describe_dir("plugin cache SKILL.md (enabled ones are sent)", plugin_skills)
        out.append(line)
        data["plugin_skills"] = d
    if repo:
        line, d = describe_dir("repo .claude/agents", sorted((repo / ".claude" / "agents").glob("*.md")))
        out.append(line)
        data["repo_agents"] = d
        line, d = describe_dir("repo .claude/skills", sorted((repo / ".claude" / "skills").glob("*/SKILL.md")))
        out.append(line)
        data["repo_skills"] = d
    out.append("")
    settings = home / "settings.json"
    if settings.exists():
        try:
            cfg = json.loads(settings.read_text(encoding="utf-8"))
            hooks = cfg.get("hooks", {}) or {}
            counts = {ev: sum(len(g.get("hooks", [])) for g in groups) for ev, groups in hooks.items()}
            out.append("Hooks in settings.json: " + (", ".join(f"{ev} x{n}" for ev, n in counts.items()) or "none") + ".")
            data["hooks"] = counts
        except ValueError:
            out.append("settings.json could not be parsed.")
    claude_json = home.parent / ".claude.json"
    servers: list[str] = []
    if claude_json.exists():
        try:
            cfg = json.loads(claude_json.read_text(encoding="utf-8"))
            servers += [f"{k} (user)" for k in (cfg.get("mcpServers") or {})]
            for proj, pv in (cfg.get("projects") or {}).items():
                for k in (pv.get("mcpServers") or {}):
                    servers.append(f"{k} (project {Path(proj).name})")
        except ValueError:
            pass
    if repo and (repo / ".mcp.json").exists():
        try:
            servers += [f"{k} (repo .mcp.json)" for k in (json.loads((repo / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers") or {})]
        except ValueError:
            pass
    out.append("MCP servers configured on disk (names only): " + (", ".join(servers) or "none") + ". Desktop-app bundled servers and connectors are not on disk; count them from section 2's deferred tool names.")
    data["mcp_servers"] = servers
    out.append("")
    return out, data


# ----------------------------------------------------------------------------
# section 5: levers
def levers(usage: dict[str, Any], anatomy: dict[str, Any], turns: dict[str, Any], disk: dict[str, Any]) -> list[str]:
    out = ["## 5. Where the leverage is", ""]
    comps = {c["component"]: c for c in anatomy.get("components", [])}

    def comp_chars(prefix: str) -> int:
        return sum(c["chars"] for n, c in comps.items() if n.startswith(prefix))

    agents = comp_chars("Agent listing")
    skills = comp_chars("Skill listing")
    instr = comp_chars("Instructions")
    tools = comp_chars("Tool schemas")
    if agents > 8_000:
        out.append(f"- Agent listing is {agents:,} chars on every request and every subagent. Cap descriptions at ~200 chars and scope domain-specific agents to the repos that use them.")
    if instr > 10_000:
        out.append(f"- CLAUDE.md instructions total {instr:,} chars. Keep only rules needed in every session; move repo-specific text to the repo CLAUDE.md and hook-enforced rules to the hook.")
    if skills > 8_000:
        out.append(f"- Skill listing is {skills:,} chars. Shorten descriptions; disable plugins not used in this project.")
    if tools > 100_000:
        out.append(f"- Up-front tool schemas are {tools:,} chars (cached, but still context). A desktop or MCP-heavy surface costs 20k+ tokens over a plain CLI session; run non-UI work where fewer tools are loaded, and keep MCP servers deferred (names only).")
    hooks = turns.get("hooks", {})
    ups = hooks.get("UserPromptSubmit")
    if ups and ups.get("median", 0) > 800:
        out.append(f"- UserPromptSubmit hooks inject a median {ups['median']:,.0f} chars per prompt (max {ups['max']:,}); each injection is re-sent for the rest of the session, so cost is quadratic in turns. Emit standing policy once per session and cap inbox-style payloads.")
    tr = turns.get("tool_results", {})
    for name, stats in tr.items():
        if stats["mean"] > 5_000 and stats["calls"] > 20:
            out.append(f"- {name} results average {stats['mean']:,.0f} chars (max {stats['max']:,}). Bound reads with limit/offset, prefer text extraction over screenshots, route sweeps through a read-only subagent.")
    if usage.get("idle_rewrites", 0) > 0:
        out.append(f"- {usage['idle_rewrites']} large mid-session cache rewrites followed an idle gap over 60 minutes: resuming a big context after the cache TTL re-writes all of it at cache-write price. Start a fresh session after long idles when context is large.")
    if usage.get("context_median", 0) > 200_000:
        out.append(f"- Median request context is {fmt(usage['context_median'])} tokens and sessions average {usage.get('requests_per_session', 0):.0f} requests; the prefix is a minority of an average request. Compact at task boundaries and keep tool output small.")
    if usage.get("after_toolsearch_median", 0) < 5_000:
        out.append("- ToolSearch loads are cheap (small cache write, no prefix invalidation): keeping MCP tools deferred is working.")
    if len(out) == 2:
        out.append("- No threshold tripped; the prefix is lean. Focus on transcript growth per session.")
    out.append("")
    out.append("Verify a change: start a fresh session, then read the first assistant line of the new transcript: cache_read + cache_creation is the new prefix. Compare with section 1's first-request median.")
    return out


# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--claude-home", default=str(Path.home() / ".claude"), help="Claude Code home (default ~/.claude)")
    ap.add_argument("--days", type=int, default=7, help="transcripts modified in the last N days (default 7)")
    ap.add_argument("--project", help="only project dirs containing this substring")
    ap.add_argument("--session", help="session id prefix for the anatomy section (default: newest main session)")
    ap.add_argument("--repo", help="repository path to include repo-level CLAUDE.md, agents, skills, .mcp.json")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", help="write all numbers to this JSON file")
    args = ap.parse_args()

    home = Path(args.claude_home)
    root = home / "projects"
    if not root.exists():
        print(f"No transcripts at {root}", file=sys.stderr)
        return 2
    sessions = discover(root, args.days, args.project)
    mains = [s for s in sessions if s.kind == "main" and s.requests]
    if not mains:
        print("No main sessions with usage in range.", file=sys.stderr)
        return 2
    if args.session:
        target = next((s for s in mains if Path(s.path).stem.startswith(args.session)), None)
        if target is None:
            print(f"No session starting with {args.session}", file=sys.stderr)
            return 2
    else:
        target = max(mains, key=lambda s: os.path.getmtime(s.path))

    report = [f"# Claude Code context cost audit", "", f"Home: {home}   window: last {args.days} days   generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}", ""]
    u_lines, u_data = usage_section(sessions, args.top)
    a_lines, a_data = anatomy_section(target)
    t_lines, t_data = per_turn_section(sessions, args.top)
    d_lines, d_data = disk_section(home, Path(args.repo) if args.repo else None)
    report += u_lines + a_lines + t_lines + d_lines + levers(u_data, a_data, t_data, d_data)
    print("\n".join(report))
    if args.json:
        Path(args.json).write_text(json.dumps({"usage": u_data, "anatomy": a_data, "per_turn": t_data, "disk": d_data}, indent=2, default=str), encoding="utf-8")
        print(f"\nJSON written to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
