# Claude Code token audit

> **Claude Code only.** This tool is specific to Anthropic's **Claude Code**
> (CLI, desktop app, IDE extensions). It reads the local session transcripts
> that Claude Code writes to `~/.claude/projects/`. It does not apply to
> Codex, GitHub Copilot, Cursor, Gemini CLI, the claude.ai web app, or direct
> Anthropic API calls — none of those produce this transcript format.

`claude_code_token_audit.py` answers "where are my tokens going?" across every
Claude Code session on this machine, broken down by:

| dimension | source field in transcript |
|---|---|
| model (`claude-opus-5`, `claude-fable-5`, …) | `message.model` |
| reasoning effort (`medium` / `high` / `xhigh` / `max`) | top-level `effort` |
| thread: **main** conversation vs **Agent-tool subagent** vs **Workflow agent** | file location under `<session>/subagents/` |
| subagent type (`Explore`, `general-purpose`, custom agents…) | `attributionAgent` |
| skill that was active | `attributionSkill` |
| session / project directory / day | `sessionId`, path, `timestamp` |

For each bucket it reports calls, uncached input, cache writes, cache reads,
output, thinking tokens, total, and an **API-list-price cost estimate**
(cache read 0.1×, 5-minute cache write 1.25×, 1-hour cache write 2×).

## Usage

No dependencies beyond Python 3.8+.

```bash
# everything on this machine, default tables
python claude_code_token_audit.py

# last week, only the cross-tabs you care about
python claude_code_token_audit.py --since 2026-08-16 --by model_effort,model_thread,agent,session

# one project, exact numbers, export every de-duplicated API call
python claude_code_token_audit.py --project watershed --raw --csv usage.csv
```

| flag | meaning |
|---|---|
| `--since` / `--until YYYY-MM-DD` | UTC date range (inclusive) |
| `--project SUBSTR` | filter on the encoded project directory name |
| `--session PREFIX` | single session id prefix |
| `--by a,b,c` | tables to print: `thread`, `model`, `effort`, `model_effort`, `model_thread`, `effort_thread`, `agent`, `skill`, `session`, `project`, `day`, `version`, `speed`, `all` |
| `--top N` / `--sessions N` | rows per table |
| `--raw` | exact integers instead of `1.2M` |
| `--csv PATH` / `--json PATH` | one row per de-duplicated API call |
| `--no-cost` | suppress cost columns |
| `--root PATH` | transcript root (default `~/.claude/projects`) |

## Why a custom parser (and what it gets right)

Verified against real transcripts on Claude Code 2.1.2xx (Aug 2026):

* **One API response is written as several JSONL lines** — one per content
  block (thinking, text, each `tool_use`). Every line repeats `message.id` and
  a `usage` object, and `output_tokens` grows across the lines. A naive
  line-by-line sum over-counts by ~2×. The script groups by `message.id` and
  keeps the line with the largest `output_tokens`.
* **Subagent transcripts are separate files**:
  `<project>/<sessionId>/subagents/agent-*.jsonl` for the Agent tool and
  `<project>/<sessionId>/subagents/workflows/wf_*/agent-*.jsonl` for the
  Workflow tool. Main-thread entries have no `agentId`; subagent entries do.
* `usage.cache_creation.ephemeral_{5m,1h}_input_tokens` gives the cache-TTL
  split, so write costs are priced exactly rather than assumed.

## Caveats

* The transcript format is **internal to Claude Code** and documented to change
  between versions. Unknown fields fall into `?` buckets rather than crashing,
  but re-verify after Claude Code upgrades.
* Claude Code deletes transcripts older than `cleanupPeriodDays` (default 30)
  in `settings.json`. Raise it, or run `--csv` on a schedule, to keep history.
* Costs are list-price estimates from the `PRICING` table at the top of the
  script. On a Pro/Max subscription they are a proxy for rate-limit
  consumption, not a bill. Update the table when models or prices change.

## Official alternatives

* **OpenTelemetry export** (`CLAUDE_CODE_ENABLE_TELEMETRY=1`) is the supported,
  forward-compatible way to get the same dimensions: the
  `claude_code.token.usage` metric carries `model`, `effort`, `query_source`
  (`main` / `subagent` / `auxiliary`), `agent.name`, and `skill.name`.
  Quick check with no collector: `OTEL_METRICS_EXPORTER=console`.
  See <https://code.claude.com/docs/en/monitoring-usage>.
* `/usage` inside Claude Code shows per-model tokens for the current session
  plus subagent/skill/MCP attribution as percentages (24h / 7d).
* `ccusage` (third-party) gives per-model and per-session reports but no
  effort or subagent split.
