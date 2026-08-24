# Codex token usage audit

> **Codex local clients only.** This tool reads retained local Codex Desktop and
> CLI rollout evidence under `~/.codex`. It does not query account billing, API
> organization usage, or another machine. Local token totals and estimated
> credits are therefore an audit aid, not billing truth.

`codex_token_usage_audit.py` answers “where are my Codex tokens going?” across
root tasks and their subagents. Its normalized ledger has one record per
turn, observed model/reasoning-effort pair, and UTC day. A turn spanning
midnight or changing model context can have multiple rows but is counted once
in overall turn totals.

| Dimension | Local source |
|---|---|
| model and reasoning effort | `turn_context.payload.model` / `.effort` |
| per-turn token delta | consecutive cumulative `token_count.info.total_token_usage` snapshots |
| root vs subagent | `session_meta.payload.source.subagent.thread_spawn` |
| parent, depth, agent path, nickname, and role | `thread_spawn` metadata, optionally enriched from `state_5.sqlite` |
| task title and missing parent edges | optional read-only `state_5.sqlite` query |
| project, client, version, day | session metadata and rollout path |

For every bucket it reports turns, positive model-call increments, input,
cached input, fresh input, output, reasoning output, total tokens, and an
estimated standard-rate Codex credit equivalent.

## Usage

Python 3.10+; no third-party packages.

```powershell
# All retained active and archived sessions; default tables
py -3 .\codex_token_usage_audit.py

# Last week, focused breakdowns
py -3 .\codex_token_usage_audit.py `
  --since 2026-08-16 `
  --by model_effort,thread,agent,task,turn,day

# One root task including all descendant subagents
py -3 .\codex_token_usage_audit.py --root-session 01a02f60 --raw

# Machine-readable snapshot for another Codex prompt
py -3 .\codex_token_usage_audit.py --since 2026-08-16 --json .\codex-usage.json

# Pure JSON on stdout for automation; task titles are omitted by default
py -3 .\codex_token_usage_audit.py --since 2026-08-16 --json -
```

Suggested periodic prompt:

```text
Run codex-token-usage-audit/codex_token_usage_audit.py with --since set to
seven days ago and --json -. Summarize total and estimated credits by model x
reasoning effort, root vs subagent, agent path, root task, turn, and day. Flag
large cached-context replay, unusually expensive turns, and unknown/unpriced
models. Compare with the previous snapshot if one is available.
```

### Flags

| Flag | Meaning |
|---|---|
| `--root PATH` | Codex data root; defaults to `CODEX_HOME`, then `~/.codex` |
| `--state-db PATH` / `--no-state-db` | override or disable optional read-only SQLite enrichment; a missing explicit override is a warning |
| `--active-only` | exclude `archived_sessions`; archived sessions are included by default |
| `--since` / `--until YYYY-MM-DD` | UTC turn date range, inclusive |
| `--project SUBSTR` | filter repository/project name or working directory |
| `--session PREFIX` | one thread, without automatically including descendants |
| `--root-session PREFIX` | one root task and every retained descendant subagent |
| `--model SUBSTR` / `--effort VALUE` | filter a model id or exact reasoning effort |
| `--agent SUBSTR` | filter agent path, nickname, or role |
| `--thread-type root\|subagent` | root tasks or subagent threads only |
| `--by a,b,c` | `thread`, `model`, `effort`, `model_effort`, `model_thread`, `agent`, `task`, `session`, `turn`, `project`, `day`, `client`, `version`, or `all` |
| `--top N` | rows per table; default `20` |
| `--raw` | exact integers instead of abbreviated K/M/B values |
| `--include-titles` | opt in to prompt-derived task titles; omitted by default |
| `--include-paths` | opt in to Codex home, cwd, and rollout paths; redacted by default |
| `--no-credits` | suppress standard-rate credit estimates |
| `--csv PATH` | normalized turn/model-effort/day ledger; use `-` for clean stdout |
| `--json PATH` | snapshot with totals, breakdowns, rows, scan stats, and warnings; use `-` for clean stdout |
| `--strict` | emit the report but return exit code `2` when scan warnings occur |

## Why cumulative turn deltas

Codex writes a `token_count` event after model activity. Each event contains:

- `total_token_usage`: cumulative counters for the thread;
- `last_token_usage`: the most recent reported model call.

`last_token_usage` can be repeated or re-emitted. Summing every occurrence can
over-count, while keeping only the final cumulative value can lose usage when a
counter resets or starts with an inherited parent baseline. The audit streams
every event in file order:

- the first event and a rebased counter use a validated `last_token_usage`;
- an exact repeated cumulative vector adds zero;
- a component-wise monotone vector adds `current - previous`;
- an all-zero reset marker adds zero and starts a new counter epoch.

`task_started` establishes the active turn immediately. A matching
`turn_context` supplies model and reasoning effort, including when it arrives
after initial usage. Each positive increment snapshots the model and reasoning
effort active at that point. Late context backfills unattributed increments,
while a later context change does not reassign earlier usage. Increments are
grouped by turn, model/effort, and UTC day, so date filters retain prior counter
state without recounting a next-day duplicate.

Token arithmetic follows these invariants:

- cached input is already included in input;
- fresh input is `input - cached input`;
- reasoning output is already included in output;
- total is input plus output, so cached and reasoning tokens are not added twice.

Each subagent has a separate rollout file and cumulative counter. The script
links those threads through `parent_thread_id`, resolves nested descendants to a
root task, and sums each thread exactly once. If copied parent history repeats a
UUID turn ID in more than one rollout, the audit keeps the copy with the most
token evidence; exact ties prefer the root copy. Non-UUID legacy labels remain
thread-scoped because they are not globally unique.

## Read-only behavior and privacy

The script opens `state_5.sqlite` with SQLite `mode=ro` and `query_only=ON`.
SQLite is optional: rollout JSONL files remain the token source of truth, while
the database only supplies titles and relationship metadata when available.
Task titles are not selected from SQLite unless `--include-titles` is supplied.
Parent resolution prefers an indexed `thread_spawn_edges` relationship, then a
direct rollout `parent_thread_id`, then nested `thread_spawn` metadata. A
conflict is surfaced as a warning rather than silently hidden.

CSV and JSON destinations are checked before writing. The CLI rejects output
paths that resolve to the same destination or collide with the state database
or a discovered rollout file, including case-insensitive Windows path aliases.

Normalized output intentionally excludes:

- user prompt and response bodies;
- system/developer instructions;
- reasoning text;
- tool arguments and results.

Task titles can contain part or all of a first prompt, so they are omitted by
default. Use `--include-titles` only when the destination should receive them.
File paths and working directories can also contain sensitive names, so raw
paths are blanked from turn records and the Codex home is redacted unless
`--include-paths` is supplied. Warning paths use `<CODEX_HOME>`, `<STATE_DB>`,
or `<ROLLOUT>` placeholders under the same default, including for explicit
sources outside the Codex home. The derived project/repository name remains.

## Credit estimate

The built-in table is a dated snapshot of standard Codex credits per 1M tokens:

| Model | Fresh input | Cached input | Output, including reasoning |
|---|---:|---:|---:|
| GPT-5.6 Sol | 100 | 10 | 500 |
| GPT-5.6 Terra | 50 | 5 | 300 |
| GPT-5.6 Luna | 5 | 0.5 | 30 |

Rates were verified on 2026-08-23 against the official
[Codex pricing and usage page](https://learn.chatgpt.com/docs/pricing). Unknown
models remain in token totals and are marked unpriced rather than assigned an
invented rate. Included-plan allowances, promotions, fast mode, and future rate
changes can make account consumption differ from this estimate.

## Caveats

- Rollout JSONL and `state_5.sqlite` are internal Codex formats and can change.
  Unknown fields are ignored; malformed, locked, missing, duplicate, or reset
  data produces warnings. Use `--strict` in scheduled jobs.
- The report covers only retained files on this machine. Deleted sessions and
  work done on other hosts are invisible.
- Active rollout files can grow while a scan is running. A later scan is the
  authoritative local snapshot for an in-progress task.
- A turn total cannot split input further into user text, instructions, history,
  file context, or tool results because Codex does not expose token counts for
  those individual context sources.

Official account/session alternatives are the
[Codex usage dashboard](https://chatgpt.com/codex/settings/usage), `/usage`,
`/status`, and `/statusline` documented in
[Codex developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli).
For a supported long-running telemetry pipeline, configure OpenTelemetry as
described in [Codex advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced).

## Tests

The offline `unittest` suite uses synthetic, prompt-free rollout fixtures:

```powershell
py -3 -m unittest discover -s .\tests -v
```

It covers inherited baselines, cumulative-delta accounting, duplicate snapshots,
cache/reasoning subset arithmetic, model/effort backfill, nested subagents,
copied-parent turn deduplication, counter resets, UTC-boundary filters,
malformed JSONL, duplicate sessions,
read-only SQLite enrichment, machine-readable output, and privacy exclusions.
It also covers unpriced turns, output-path collision protection, full-ID
breakdown isolation, and explicit missing-state warnings.
