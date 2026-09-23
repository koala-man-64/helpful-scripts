---
name: context-cost-audit
description: Use to measure what every Claude Code request carries (system prompt, tool schemas, CLAUDE.md, agent and skill listings, hooks, MCP, transcript) from real transcripts and rank what to trim.
---

# Context cost audit

Measures, from the transcripts Claude Code already writes, what each API request carries and why, then ranks the levers. Standard-library Python only; works on Windows, macOS, Linux; no API key needed. Copy this folder into `~/.claude/skills/` or a repo's `.claude/skills/` to use it elsewhere.

## Run

```bash
python "<this folder>/scripts/context_cost_audit.py" --days 7
```

Options: `--project <substring>` to limit to one project dir, `--session <id-prefix>` to pick the session whose prefix is dissected (default: newest), `--repo <path>` to include repo-level CLAUDE.md, agents, skills and `.mcp.json`, `--json out.json` for the raw numbers, `--claude-home` if `~/.claude` is elsewhere.

## What the report contains and how to read it

1. **Real usage.** Deduplicated API calls (one response is written as several jsonl lines sharing `message.id`; keep the line with the largest `output_tokens`). Context per request is `input + cache_creation + cache_read`. The first request of a session is the fixed prefix before the user's first word. `cache_read` on that first request is the cross-session static block (tool schemas plus the static system prompt) when another session ran inside the cache TTL; `cache_creation` is the session-specific block. Read the by-project table: repos with hooks, repo CLAUDE.md, or local agent libraries show a larger prefix.
2. **Prefix anatomy.** Exact character sizes from `attachment` records in one transcript: `prompt_snapshot` (system prompt blocks split at `__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__`, plus the `tools` array when recorded), `instructions` (CLAUDE.md files), `agent_listing_delta`, `skill_listing`, `mcp_instructions_delta`, `deferred_tools_delta`, `hook_additional_context`, and the small environment reminders. Tokens are allocated from the measured first request: block totals are measured, per-row tokens are proportional to characters (estimates). Older Claude Code versions may lack these attachments; then only section 4 applies.
3. **Per-turn additions.** Tool results by tool name (mapped through the preceding `tool_use` id), hook-injected text per event, skill invocations. Everything here is re-sent with every later request in the session, so per-turn overhead compounds.
4. **On-disk surface.** Which files produce the listings: global and repo CLAUDE.md, agent and skill frontmatter descriptions (only `name` and `description` are sent; bodies load on use), hooks in settings.json, MCP servers by name.
5. **Levers.** Threshold-based findings with the standard fixes.

## Facts that make the numbers trustworthy

- Prior-turn thinking is stripped from later requests; only text and tool calls persist.
- ToolSearch loading a deferred tool does not invalidate the prefix cache (check the "after ToolSearch" row; a small number means deferral is working).
- Large `cache_creation` mid-session after an idle gap longer than the cache TTL means the whole context was re-written at cache-write price; a fresh session is cheaper than resuming a big one after a long idle.
- Subagents pay their own prefix (system prompt, tools, agent listing) on every call; the parent only receives their final text.

## Natural experiments worth running

- Same config, CLI terminal vs desktop app: the difference in first-request prefix is the cost of app-bundled tools and skills.
- Same repo before and after trimming: first-request `cache_creation` should drop by the trimmed characters divided by the session block's chars-per-token.
- A session with and without a hook-enabled repo: the difference in per-turn `cache_creation` is the hook overhead.

## Standard fixes, in order of tokens saved per request

1. Agent descriptions: cap at ~200 characters, scope domain-specific agents to the repos that use them (they are also paid by every subagent).
2. CLAUDE.md: keep only rules needed in every session; move repo rules to the repo file and hook-enforced rules into the hook.
3. Hooks: emit standing policy once per session (re-emit after compaction), cap inbox-style payloads, avoid Stop-hook blocks that cost a full extra turn.
4. Surface: run non-UI work where fewer tools load; disconnect connectors not needed for code; keep MCP tools deferred.
5. Transcript: bound reads with limit/offset, prefer text extraction to screenshots, route sweeps through a read-only subagent, compact at task boundaries, start fresh after long idles.

Verify after each change with a fresh session and the first assistant line of its transcript.
