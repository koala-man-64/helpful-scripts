# Codex / Claude Code workflow hooks

This folder holds the repo-local workflow hooks for `helpful-scripts`. They are the same
shared hook set the sibling asset-allocation repos run, kept here so the agent, skill, and
hook definitions in this repo stay in sync with each other.

Hooks are wired in [`../hooks.json`](../hooks.json). Each entry runs the script through
`python3` on POSIX and `py -3` on Windows, resolving the repo root with
`git rev-parse --show-toplevel`, so the same config works from a clone, a worktree, or a
subdirectory.

## The hooks

| File | Event | What it does |
| --- | --- | --- |
| `session_start_team_context.py` | `SessionStart` (`startup`, `resume`, `compact`) | Emits repo name, root, branch, working-tree cleanliness, and which core team agent definitions are missing, plus the standing reminders to route work through `delivery-orchestrator-agent` and to finish on a task-owned branch. |
| `user_prompt_submit_router.py` | `UserPromptSubmit` | Classifies the prompt into a lane (`finish`, `ci-pipeline`, `production-incident`, `azure-boards-bookkeeping`, `repo-cleanup`, `docs`, the trading lanes, …) and injects the required agent chain, whether Azure DevOps tracking and the git finish workflow apply, and the contracts-repo-first routing reminder. |
| `pre_tool_use_bash_guard.py` | `PreToolUse` | Allow/ask/deny decision for shell commands. See the guard rules below. |
| `stop_team_closeout.py` | `Stop` | After a turn that did substantive work, blocks closeout until the final message accounts for validation, the git finish workflow (commit, push, PR, merge) or its exact blocker, and the `code-drift-sentinel` / `software-testing-validation-architect` gates. |
| `stop_gateway_bookkeeper_recap.py` | `Stop` | Blocks closeout on auditable work until the final message carries a `Bookkeeper Recap` section. |
| `stop_runtime_ownership_closeout.py` | `Stop` | Repo-local hook: blocks closeout on substantive work until the final message resolves `runtime-ownership-enforcer` as completed, not applicable, or blocked. |
| `hook_utils.py` | — | Shared library: hook I/O, git helpers, prompt classification, and the JSON response builders. Not a hook itself. |

`hook_utils.py` is imported as a top-level module by its siblings, so all hook scripts must
stay in this directory together.

## What the bash guard blocks

`pre_tool_use_bash_guard.py` denies, rather than asks:

- History- and work-destroying git commands — `git reset --hard`, `git clean -fd`,
  `git checkout --`, `git restore .`, `git branch -D`, and force pushes that are not
  `--force-with-lease`.
- Direct pushes to protected branches (`main`, `master`, `trunk`, `develop`, `staging`,
  `production`), including the `HEAD:branch` form.
- Commands that would print secret-bearing values — echoing a token, PAT, OAuth code,
  password, connection string, or private key.
- Recursive deletes and moves whose target resolves outside the repository root.
- Approving production deployment gates. Production approval stays with the user; the guard
  expects it to be recorded as the remaining blocker instead.

Everything else is allowed with an explicit reason, so the decision is visible in the
transcript either way.

## Verifying a hook

Each hook reads a JSON payload on stdin and writes a JSON decision on stdout. To exercise
one directly:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | py -3 .codex/hooks/pre_tool_use_bash_guard.py
```

A quick syntax check across the set:

```bash
py -3 -m py_compile .codex/hooks/*.py
```

## Provenance and drift

Synced 2026-07-28 from the live hook set at `~/.claude/hooks`, which was the newest copy at
the time (2026-07-27) — newer than the snapshots under `exports/codex-hooks/`, which remain
historical per-repo exports and are not updated by this sync.

SHA-256 of the files as installed:

| File | SHA-256 |
| --- | --- |
| `hook_utils.py` | `dc76c8ac4c56c4fa1a67cff25169d332d2783b4c247703001dbfd28a6e219df8` |
| `pre_tool_use_bash_guard.py` | `b2ffaf3311af83fca0d2fdb205671f10bdf91ed20816b59031ea2588d5f4c1ec` |
| `session_start_team_context.py` | `60e3340fc4f70048106d96a1dba838000ff5b5e6ca3d78802dbecf1d6e47c11c` |
| `stop_gateway_bookkeeper_recap.py` | `1844014507bc8f8bc7599242fccb7812eb39188ce2e0070656d496ae6229094d` |
| `stop_runtime_ownership_closeout.py` | `b135f0f2f3a3d300020d749edb6f0319a7c237033044f3800d4bac649630de09` |
| `stop_team_closeout.py` | `c29ddcecf1e180d58f94d1d09f4449566f0ef22f64891c8aed1ce9a2673f722a` |
| `user_prompt_submit_router.py` | `b8c4dd98d4bac0704c76c5b4f79d0401f234a93aa69dbdbe4ef5297473e57cac` |

Regenerate with `sha256sum .codex/hooks/*.py` after changing a hook, and update the table so
drift against the other repos stays detectable.

## Related definitions

- Agents: [`../../.claude/agents`](../../.claude/agents)
- Skills: [`../../.claude/skills`](../../.claude/skills) (Claude) and
  [`../skills`](../skills) (Codex)
