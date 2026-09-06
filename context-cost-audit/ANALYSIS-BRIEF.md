# Context cost analysis brief

Purpose: measure what every model request carries in an agentic coding tool (Codex, Antigravity, Claude Code, or similar), attribute those tokens to the configuration and behaviour that produce them, and rank what to trim. This brief describes the analysis exactly as it was done for Claude Code on 2026-09-06 so another agent can repeat it on its own system. It is tool-agnostic in method; the Claude Code specifics are given as worked examples of what to look for.

Deliverables: a report (sections 1 to 5 below), a reusable script that regenerates it, and an optimization plan with before/after verification.

## 0. Ground rules

- Read-only investigation first. No config edits until the report exists and the operator has approved a plan.
- Measure, do not guess. Every number is either measured (from per-request usage the tool logged, or exact character counts of stored text) or allocated (a measured total split proportionally by characters). Label which is which. Never invent token counts; a tokenizer is not required.
- Anchor on per-request usage records the tool already writes. Character counts of files are secondary evidence that explain the usage numbers.
- Never print secrets while inspecting config (API keys, tokens, connection strings in MCP server configs or env files). Report names and sizes only.
- Cross-check the key numbers with an independent re-derivation before reporting them.

## 1. Questions the analysis answers

1. What is in one request, component by component, and how big is each component?
2. What is the fixed prefix that arrives before the user's first word, and how much of it is cached across sessions vs rebuilt per session?
3. What does each later turn add, and which of those additions persist for the rest of the session?
4. What does an average request look like (median and p90 context, output, cache share), and how much of it is prefix vs accumulated conversation?
5. Which configuration files and behaviours produce the cost, and in what order should they be trimmed?
6. How will a change be verified?

## 2. Data sources to locate

Find the equivalents of each of these in the target system before doing anything else.

| Need | Claude Code example | What to look for elsewhere |
|---|---|---|
| Per-request usage log | `~/.claude/projects/<project>/<session>.jsonl`, lines `type == "assistant"` with `message.usage` (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, `output_tokens_details.thinking_tokens`), `message.id`, `timestamp`, `message.model` | Codex: session rollout JSONL under `~/.codex/sessions/...` with `token_count` events carrying input / cached input / output / reasoning tokens per turn. Antigravity: its conversation or trajectory logs; find the per-request usage fields. If usage is only cumulative, difference consecutive records. |
| Sub-agent transcripts | `<project>/<session>/subagents/**/agent-*.jsonl` | Any child-run logs; they pay their own prefix. |
| Stored prompt components | `type == "attachment"` records: `prompt_snapshot` (system prompt blocks, `tools` array), `instructions` (CLAUDE.md contents), `agent_listing_delta`, `skill_listing`, `mcp_instructions_delta`, `deferred_tools_delta`, `hook_additional_context` | Any place the tool logs the assembled system prompt, tool list, or injected instructions. If absent, fall back to measuring the files on disk and the tool descriptions in the binary or package. |
| Global instructions | `~/.claude/CLAUDE.md`; repo `CLAUDE.md`; directory-level files | Codex: `~/.codex/AGENTS.md` and repo `AGENTS.md`; Antigravity: its rules or instructions files. |
| Agent and skill definitions | `~/.claude/agents/*.md`, `~/.claude/skills/*/SKILL.md`, plugin caches, repo `.claude/agents`, `.claude/skills`; only frontmatter `name` + `description` is sent every request, bodies load on use | Equivalent skill or sub-agent registries; determine what part is always sent vs loaded on demand. |
| Hooks | `~/.claude/settings.json` `hooks` section and the scripts it runs; injected text appears as `hook_additional_context` | Codex hooks or notify scripts; any pre-prompt or post-tool injection mechanism. |
| MCP servers and tools | `~/.claude.json` `mcpServers`, repo `.mcp.json`, gateway registries (for example Docker MCP `registry.yaml`), app-bundled servers not on disk | Codex `config.toml` `mcp_servers`; Antigravity MCP config. Distinguish tools whose full schema is sent up front from tools that are deferred (names only). |
| Tool schemas | `prompt_snapshot.tools` when present; otherwise strings in the tool binary | Whatever the tool sends as the `tools` array; for open-source tools read the source. |

## 3. Method

### 3.1 Real usage (section 1 of the report)

1. Parse every transcript modified in the window (7 days used; 30 for a quieter machine).
2. Deduplicate streamed usage. In Claude Code one response is written as several lines sharing `message.id`, and `output_tokens` grows across them; group by id and keep the max. Check whether the target tool does the same.
3. Per request: context = uncached input + cache write + cache read. Output and reasoning tokens separately.
4. Per session: the first request's context is the fixed prefix before the user's first word. Record cache read and cache write separately: on a first request, cache read is the block identical to a previous session within the cache TTL (tool schemas plus static system prompt), cache write is the session-specific block.
5. Compute: sessions, requests, requests per session, first-request prefix median / min / max and by project, context mean / median / p90 / max, growth per request, output mean / median, thinking mean, percent of context served from cache.
6. Cache events: count mid-session cache writes above 20k tokens and how many follow an idle gap longer than the cache TTL (60 minutes used). Record the cache write on the request after a deferred-tool load to see whether loading invalidates the prefix.
7. Sub-agents: count transcripts, median first-request prefix, share of all tokens.
8. Compactions or context resets: count them.

### 3.2 Prefix anatomy (section 2)

1. Pick one representative session (newest, or one per surface: terminal, desktop app, hooked repo).
2. Measure the exact characters of each component from stored prompt records: system prompt static vs dynamic parts, tool schema block and tool count, instructions files, agent listing, skill listing, MCP server instructions, deferred tool names, session-start hook context, small reminders (environment, date, model).
3. If stored records do not exist, measure the same things from disk and from the tool's binary or source, and say so.
4. Allocate tokens: block totals are measured (cache read = static block, cache write = session block on the first request); within a block, split proportionally by characters. Report the effective chars-per-token per block. Observed on Claude: JSON tool schemas about 4.25 chars/token; instructions and listings about 2.65 chars/token (CJK words, hyphenated names, UUID-prefixed tool names tokenize densely).

### 3.3 Per-turn additions (section 3)

1. Tool results: map each result to its tool name via the preceding tool call id. Per tool: calls, total chars, mean, p90, max.
2. Hook injections: per event, count, median, mean, max chars.
3. Skill or slash-command invocations: chars of the injected body.
4. Assistant output: text and tool-call chars; note whether prior-turn reasoning is resent (in Claude it is stripped).
5. State the compounding rule: everything added on turn n is resent on every later request of the session.

### 3.4 On-disk surface (section 4)

For each always-sent file: path, chars, words; for agents and skills the description chars per item and the longest ones; hooks configured per event; MCP servers by name and scope; tool counts per server where a registry exists.

### 3.5 Natural experiments (run at least two)

- Same config, terminal vs desktop or IDE surface: the prefix difference is the cost of surface-bundled tools and skills. Claude: 54 to 57k vs 74 to 91k tokens.
- Repo with hooks or a large local agent library vs a plain directory: Claude showed +14k tokens for a repo with 56 local agents and 57 skills.
- Before and after a trim, fresh session each time: cache write on the first request should fall by trimmed chars divided by the session block's chars-per-token.
- Load a deferred tool mid-session: a small cache write and an unchanged cache read means deferral works.

### 3.6 Independent verification

Have a second process re-derive first-request prefix median, per-request median context, mean output, and one tool-result statistic from scratch with its own code. Accept within 10 percent. On Claude the verifier matched within 1 percent.

## 4. Report format

```
# <tool> context cost audit
Home, window, generated-at

## 1. Real usage from transcripts        (table of metrics; by-project prefix table)
## 2. Prefix anatomy of session <id>     (component | chars | block | allocated tokens; effective chars/token)
## 3. What each later turn adds          (tool results by tool; hook injections by event; skill invocations)
## 4. Always-on surface on disk          (file | items | description chars | longest)
## 5. Where the leverage is              (threshold findings, one line each, largest tokens/request first)
Verify: fresh session, read the first request's usage; compare with section 1.
```

Keep numbers in tables. Say for every number whether it is measured or allocated.

## 5. Levers, in the order they paid off on Claude Code

1. Agent descriptions (always sent, also paid by every sub-agent): cap at about 200 chars, scope domain-specific agents to the repos that use them, merge near-duplicates. Claude: 22,337 chars to 9,903.
2. Global instructions file: keep only rules needed in every session; move repo rules to the repo file and hook-enforced rules into the hook. Claude: 22,955 chars to 14,205.
3. Hooks: emit standing policy once per session and again after compaction; cap inbox-style payloads; avoid stop-hook blocks, which cost a full extra turn. Claude: per-turn router text 2,014 chars to 406; peer-inbox cap 8 KB to 2 KB.
4. Surface: run non-UI work where fewer tools load; disconnect connectors not needed for code; keep MCP tools deferred (names only). This was the largest single number (20k+ tokens per request) and is a habit, not a config edit.
5. Transcript growth: bound file reads, prefer text extraction to screenshots, route sweeps through a read-only sub-agent, compact at task boundaries, start a fresh session after long idles when context is large (resuming rewrites the whole context at cache-write price).

## 6. Reference baseline from the Claude Code run (for comparison)

| Metric | Value |
|---|---|
| Sessions / requests analysed (7 days) | 77 / 16,432 |
| First-request prefix: desktop scratch / hooked repo / terminal | 79k / 77 to 91k / 54 to 57k tokens |
| Static block (tool schemas 179k chars, 44 tools, plus 1.2k-char static prompt) | 42.4k tokens, identical across projects |
| Session block: CLAUDE.md 22.7k chars, agent listing 27.4k, skill listing 16.4k, dynamic system prompt 12.8k, deferred names 8.4k, MCP instructions 6.1k | 33 to 37k tokens |
| Median / p90 request context | 351k / 721k tokens |
| Context served from cache | 97.3% |
| Requests per session | 213 |
| Large mid-session cache rewrites, share after >60 min idle | 461, 65% |
| Cache write after a deferred-tool load (median) | 2.4k tokens (no invalidation) |
| Hook text per prompt in hooked repos (median / max) | 1,732 / 8,170 chars |
| Read tool result (mean / max) | 8.4k / 313k chars |
| Sub-agent first-request prefix (median) | 51.6k tokens |

## 7. Pitfalls

- Streamed usage lines over-count about 2x if not deduplicated.
- Reasoning tokens are billed as output but transcripts may store only a summary, so a chars-per-token ratio computed from assistant output is not a general constant.
- A cold cache on the first request merges the static and session blocks; use the boundary marker or a second session within the TTL to split them.
- Tool names with UUID or long server prefixes are token-expensive even when only the names are sent.
- Config files next to MCP definitions often contain secrets; read names and sizes only.
- Numbers from files on disk explain, but never replace, the usage records the tool logged.

## 8. Reference implementation

A stdlib-only Python script that produces this report for Claude Code is in this folder (`scripts/context_cost_audit.py`, with `SKILL.md` next to it; copy the folder into `~/.claude/skills/` to use it as a Claude Code skill). Porting it to another tool means replacing the transcript parser (record types and usage field names) and the on-disk scanner (paths of instructions, agents, skills, hooks, MCP config); the statistics, allocation, and report sections carry over unchanged.
