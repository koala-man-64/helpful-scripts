# Recreate a Claude subagent model-evaluation ladder

## Goal

Build a portable Claude Code hook that routes bounded subagent work through this
preference and escalation order:

```text
Haiku -> Sonnet -> Opus
```

Here, `->` means "try to justify the lowest viable tier first." It is not a
claim that Haiku has greater capability than Sonnet or Opus.

The hook should make Haiku the default leaf-worker tier, require a concrete
Haiku blocker before Sonnet, and require concrete Haiku and Sonnet blockers
before Opus. It must reject an invalid request instead of silently promoting
it. A failed lower-tier attempt can support a new higher-tier contract, but the
parent must make that decision explicitly.

This guide is documentation only. It does not install or modify live hooks.
For an implementation handoff, give Claude access to this folder and paste the
prompt in
[claude-code-model-evaluation-prompt.md](claude-code-model-evaluation-prompt.md).

## What this evaluates

Keep three different concerns separate:

| Concern | Covered by the hook? | Purpose |
| --- | --- | --- |
| Routing-contract validation | Yes | Require bounded scope, verification, decomposition, and lower-tier blockers. |
| Model selection | Yes | Rewrite a valid subagent spawn to `haiku`, `sonnet`, or `opus`. |
| Model-output quality | No | Measure correctness, verification, rework, latency, and cost in an offline evaluation harness. |

Do not call another model from the latency-sensitive `PreToolUse` path to grade
the spawn. That adds cost, nondeterminism, recursion risk, and a new failure
dependency before every delegated task. Keep the hook deterministic; run
comparative output evaluation separately.

## Locked ladder policy

| Tier | Appropriate shape | Required lower-tier blockers |
| --- | --- | --- |
| `haiku` | One precise outcome, narrow scope, resolved decisions, focused verification, low blast radius | None |
| `sonnet` | Bounded implementation, mechanical work, or a read-heavy investigation needing more context or local reasoning | A specific `haiku` blocker |
| `opus` | Architecture, security, production, migration, data-integrity, or cross-repository risk | Specific `haiku` and `sonnet` blockers |

The policy is ascending and exhaustive:

```python
TIER_ORDER = ("haiku", "sonnet", "opus")
TIER_MODEL = {
    "haiku": "haiku",
    "sonnet": "sonnet",
    "opus": "opus",
}
```

Fable is intentionally outside this policy. This guide evaluates only the
requested Haiku -> Sonnet -> Opus ordering. Adding Fable or any other model
requires a separately designed and evaluated policy.

Model aliases are intentionally used here. They are portable, but the exact
model version behind an alias varies by provider and can change. If exact
reproducibility matters, pin the provider-specific model IDs after verifying
availability on the target account.

## Reference flow

```text
Claude proposes an Agent/Task spawn
                |
                v
      Is this repository managed? ---- no ----> emit nothing
                |
               yes
                v
    Is the task contract valid? ------ no ----> deny with stable reason code
                |
               yes
                v
  Are all lower-tier blockers present? no ----> deny; never auto-promote
                |
               yes
                v
 Rewrite only model + strip envelope + record redacted routing facts
```

## Local audit observations, not portable proof

This design was informed by a user-level Claude Code installation inspected
while the guide was authored. The observation found a settings registration,
a deterministic Python policy and gate, unit tests, a bounded decision log,
and an explicit managed-repository allowlist. No source repository, release
manifest, or installer was present in the audited active installation.

Those host files, repository identities, and test output are deliberately not
packaged here. They are historical context, not evidence that another host is
compatible or complete. Re-audit the target host, retain its command output as
delivery evidence, and create version-controlled source plus a repeatable
installer. Merely adding this documentation does not activate routing in this
or any other repository.

## Recommended source layout

Keep the authoritative implementation in a repository, separate from the
installed user files:

```text
claude-agent-ladder/
  VERSION
  manifest.json
  README.md
  src/
    agent_ladder.py
    hook_io.py
    pre_tool_use_agent_ladder.py
    session_start_ladder_context.py
  tests/
    test_agent_ladder.py
  scripts/
    install.ps1
    install.sh
```

The installer should copy a versioned release into the active Claude
configuration directory, verify hashes, merge only the required hook entries,
and support `--dry-run`, `--check`, and rollback. Never make an untracked home
directory the only source of truth.

## Hook-code trust boundary

Claude command hooks execute with the user's privileges. Treat hook source and
installation as executable-code delivery, not as a settings-only change:

- Install only a reviewed, versioned release from the tracked owning source.
- A digest generated from the same untrusted checkout is an integrity check,
  not provenance. Prefer a signed release or compare against an expected
  digest supplied through an independent trusted channel when available.
- Use immutable release identifiers, absolute command paths, least-privilege
  file permissions, and a dedicated bounded log location.
- The hook and installer must not inspect unrelated files, capture environment
  values, or record prompt/task text, credentials, or other sensitive content.
- If the installed hook or merged settings drift from the reviewed manifest,
  disable this hook's matcher first, preserve the evidence, and treat the
  mismatch as an incident until the trusted release is restored.

## Prerequisites

1. Record `claude --version` and the provider in use.
2. Read the current official hook, settings, subagent, and model documentation
   linked below. Claude Code evolves quickly; do not assume another host has the
   same tool names or response behavior as the machine that informed this guide.
3. Confirm the target hook scope:
   - `~/.claude/settings.json` is user-level and applies across local projects.
   - `.claude/settings.json` is project-level and can be committed.
   - User and project hook entries merge; one does not replace the other.
4. Discover the actual subagent tool names from debug/event evidence. The
   matcher must be based on the target host, not a copied example.
5. Verify that `haiku`, `sonnet`, and `opus` are permitted by the account's
   model allowlist.
6. Inspect `CLAUDE_CODE_SUBAGENT_MODEL`. Claude Code gives that environment
   variable higher precedence than a per-invocation model, so it must be unset
   or intentionally aligned with this ladder.
7. Use Python 3.10 or newer for the reference design, or adjust its syntax for
   the chosen runtime.
8. Back up every non-empty settings file before modifying it.

## Task contract

Every managed subagent prompt begins with a versioned JSON envelope. The
envelope is parent-only routing metadata and is stripped before the worker
receives the task.

```xml
<claude_subagent_task_v1>
{
  "tier": "haiku",
  "objective": "One precise outcome",
  "scope": ["path or surface the worker may inspect or change"],
  "acceptance_checks": ["a concrete verification the parent will run"],
  "constraints": ["Do not spawn another agent"],
  "depends_on": [],
  "decomposition_attempted": true,
  "lower_tier_blockers": {}
}
</claude_subagent_task_v1>

Perform the bounded task here.
```

Required validation:

- The envelope must be the first content in the prompt.
- Its body must parse as one JSON object.
- `tier` must be `haiku`, `sonnet`, or `opus`.
- `scope` and `acceptance_checks` must contain non-empty strings.
- `decomposition_attempted` must be `true`.
- `depends_on` must be absent or empty; resolve dependencies in the parent.
- Every tier below the selected tier must have a non-empty, specific blocker.
- An explicit model must agree with the selected tier.
- An implicit or full-history fork must be rejected.
- A read-only constraint must select a structurally read-only agent type when
  the target host exposes one.

### Haiku example

```xml
<claude_subagent_task_v1>
{
  "tier": "haiku",
  "objective": "List the files that define the payment retry setting",
  "scope": ["src/config", "tests/config"],
  "acceptance_checks": ["Return file paths and defining symbols"],
  "constraints": ["Read-only", "Do not spawn another agent"],
  "depends_on": [],
  "decomposition_attempted": true,
  "lower_tier_blockers": {}
}
</claude_subagent_task_v1>
```

### Sonnet example

```xml
<claude_subagent_task_v1>
{
  "tier": "sonnet",
  "objective": "Repair retry parsing and add focused regression tests",
  "scope": ["src/config/retry.py", "tests/config/test_retry.py"],
  "acceptance_checks": ["Focused tests pass", "Malformed values fail clearly"],
  "constraints": ["No dependency changes", "Do not spawn another agent"],
  "depends_on": [],
  "decomposition_attempted": true,
  "lower_tier_blockers": {
    "haiku": "The behavior spans parsing and error-boundary tests; this is not one isolated edit"
  }
}
</claude_subagent_task_v1>
```

### Opus example

```xml
<claude_subagent_task_v1>
{
  "tier": "opus",
  "objective": "Design the production migration boundary and rollback proof",
  "scope": ["architecture", "migration plan", "rollback evidence"],
  "acceptance_checks": ["No unresolved data-integrity risk", "Rollback is testable"],
  "constraints": ["No production changes", "Do not spawn another agent"],
  "depends_on": [],
  "decomposition_attempted": true,
  "lower_tier_blockers": {
    "haiku": "The task requires tradeoffs across multiple ownership boundaries",
    "sonnet": "A wrong decision can corrupt production data and needs architecture-level judgment"
  }
}
</claude_subagent_task_v1>
```

Reject vague blockers such as `too hard`, `needs a better model`, or `important`.
The hook can enforce non-empty blockers mechanically; tests and review must
judge whether the examples and operational policy make those blockers useful.

## Hook behavior

### Input

A command hook reads JSON from standard input. For a matching subagent tool,
inspect the complete `tool_input` and preserve every unrelated field.

The target schema is host-version-specific. At minimum, the audited version
uses:

```json
{
  "tool_name": "Agent",
  "tool_input": {
    "subagent_type": "Explore",
    "prompt": "<claude_subagent_task_v1>...</claude_subagent_task_v1>\nTask",
    "model": "haiku"
  }
}
```

### Accepted spawn

Clone the complete input object, set only `model`, strip the leading contract
from `prompt`, and return the full replacement under `updatedInput`. The shape
below is a security-conservative canary example, not a copy-paste claim for all
Claude Code versions:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "Confirm the rewritten subagent model and input.",
    "updatedInput": {
      "subagent_type": "Explore",
      "prompt": "Task",
      "model": "haiku"
    },
    "additionalContext": "Subagent ladder routed this bounded task to haiku."
  }
}
```

`updatedInput` replaces the entire tool input, so dropping an unrelated field
can change behavior. Current official guidance pairs a rewrite with `ask` or
`allow`: `ask` surfaces the modified call for confirmation, while `allow`
auto-approves it. Never add `allow` merely to make rewriting work. Some host
versions may support omitting a permission decision and continuing the normal
permission flow, but that behavior is not portable. Record and test the exact
response shape for the installed version; block enforcement until a real-host
smoke test proves both the rewrite and the intended permission behavior.

### Rejected spawn

Return a stable ASCII reason code and a corrective explanation:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Sonnet requires a Haiku blocker. [LADDER_MISSING_BLOCKER]"
  }
}
```

Use stable reason codes such as:

- `LADDER_MISSING_ENVELOPE`
- `LADDER_MALFORMED_ENVELOPE`
- `LADDER_UNKNOWN_TIER`
- `LADDER_NO_DECOMPOSITION`
- `LADDER_EMPTY_SCOPE`
- `LADDER_MISSING_ACCEPTANCE`
- `LADDER_UNRESOLVED_DEPENDENCY`
- `LADDER_MISSING_BLOCKER`
- `LADDER_MODEL_CONFLICT`
- `LADDER_READONLY_TIER_VIOLATION`
- `LADDER_FULL_HISTORY_FORK`
- `LADDER_OK`

Keep Unicode out of executable reason codes and machine-facing output.

### Unmanaged scope

When the current repository is outside the configured scope, emit no JSON and
exit successfully. Do not deny, rewrite, or log unrelated projects.

Use a canonical repository identity, preferably the normalized `origin` URL,
rather than a worktree directory name. Worktree names are task names and are
not reliable repository identities.

## Privacy-safe evidence

Append one bounded JSONL record per managed decision. Store only:

```text
timestamp
repository identifier
decision: routed or denied
requested tier
selected model, when routed
selection source
subagent type
stable reason code
policy/release version
```

Never store the prompt, objective, scope, acceptance checks, constraints, tool
output, source code, authentication material, full local paths, or runtime
environment values.

Cap or rotate the log. Logging failure should be observable when possible, but
it must not grant a spawn that the contract gate denied. Decide explicitly
whether evidence failure blocks enforcement; do not let an accidental
exception determine policy.

## Claude settings registration

For a user-global Windows installation, merge a matcher group like this into
`%USERPROFILE%\.claude\settings.json` or the active `CLAUDE_CONFIG_DIR`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Task",
        "hooks": [
          {
            "type": "command",
            "command": "py",
            "args": [
              "-3",
              "C:\\Users\\USER\\.claude\\hooks\\pre_tool_use_agent_ladder.py"
            ],
            "timeout": 10,
            "statusMessage": "Checking subagent model ladder"
          }
        ]
      }
    ]
  }
}
```

On macOS or Linux, use the absolute installed path and the verified Python
executable, for example `python3` with the script path in `args`.

Do not replace the complete `hooks` object. Merge this matcher alongside every
existing handler and verify that a second installer run makes no change.

Optionally register a `SessionStart` command that injects the ladder summary,
but only when the current repository is managed. The enforcement hook remains
authoritative; session text is explanatory guidance.

## Effort and turn limits

This ladder selects models only. Keep effort and tool capability separate.

- A subagent definition can specify `effort`, `maxTurns`, `tools`,
  `disallowedTools`, and `permissionMode`.
- If `effort` is omitted, it inherits the session setting.
- The audited reference advertises approximately 12 Haiku, 30 Sonnet, and 60
  Opus turns, but those counts are advisory and do not enforce a limit.
- If you need tier-specific effort or tool boundaries, create tested custom
  agent definitions and document their interaction with the per-invocation
  model. Do not claim the hook injected fields it cannot control.

## Offline model-output evaluation

Use a separate, versioned scenario corpus to determine whether the ladder is
choosing the lowest model that reliably succeeds.

Each scenario should contain:

```text
scenario ID and version
bounded task and allowed context
expected artifacts
executable or human-reviewed acceptance checks
expected lowest passing tier
prohibited behavior
maximum attempts and timeout
```

Run in the requested order:

1. Haiku
2. Sonnet only when Haiku fails the recorded acceptance check
3. Opus only when Sonnet also fails

For comparative benchmarking, run all three independently against identical
inputs; do not give higher tiers the failed model's hidden reasoning. Record
the resolved model ID, pass/fail, verification result, rework, latency, usage,
and cost. Never use a model's confident prose as the success criterion.

Starter scenarios should cover:

- durable fix versus a flashy demo;
- retrying without new evidence or novelty;
- disproportionate testing for a narrow change;
- unsupported completion claims;
- unnecessary cleanup or scope expansion;
- malformed and missing task contracts;
- mixed signals that tempt an unjustified Opus selection;
- a failed Haiku attempt that legitimately supports Sonnet;
- a failed Sonnet attempt that legitimately supports Opus.

Track at least:

- lowest-tier pass rate;
- false promotion and under-routing rates;
- verification success;
- rework and promotion frequency;
- latency and cost;
- unsupported completion rate.

Do not change enforcement thresholds from anecdotes. Version the corpus and
policy together, compare results, and preserve the prior release for rollback.

## Validation checklist

### Static and unit validation

- [ ] Settings JSON parses and every existing hook remains present.
- [ ] The configured interpreter and installed script paths exist.
- [ ] Tier order is exactly `haiku`, `sonnet`, `opus`.
- [ ] Haiku routes without blockers.
- [ ] Sonnet requires exactly the Haiku blocker.
- [ ] Opus requires both lower-tier blockers.
- [ ] Missing or malformed contracts are denied.
- [ ] Unknown tiers and explicit-model conflicts are denied.
- [ ] Full-history/implicit forks are denied.
- [ ] Unresolved dependencies are denied.
- [ ] Read-only tasks require a read-only agent type when supported.
- [ ] The accepted rewrite preserves unrelated input fields.
- [ ] No rejected spawn is silently promoted.
- [ ] Unmanaged repositories produce no output and no log entry.
- [ ] Logs contain routing facts only and respect their size cap.
- [ ] Installer dry-run, first install, check, second-install no-op, and rollback pass.
- [ ] Manifest and installed-file hashes match.

### Real user-path validation

Static tests are not proof that Claude Code applied the rewrite.

1. Start a fresh Claude Code session in one canary repository.
2. Enable debug/event logging appropriate for the installed version.
3. Spawn one valid Haiku contract and confirm the actual subagent model.
4. Spawn an invalid Sonnet contract and confirm it is denied.
5. Spawn a valid Sonnet contract and confirm the actual subagent model.
6. Repeat for Opus with both blockers.
7. Confirm an unmanaged repository is untouched.
8. Inspect the evidence log and prove that no task text was retained.
9. Confirm `CLAUDE_CODE_SUBAGENT_MODEL` and `availableModels` did not override or
   substitute the requested tier.

Report source, release, installation, unit tests, hook decision, and actual
subagent model as independent evidence. None implies the next.

## Rollout

1. **Shadow:** validate contracts and log the recommended tier without denying
   or rewriting.
2. **Canary:** enforce in one low-risk repository; set explicit rollback
   criteria and inspect real user-path evidence.
3. **Enforce:** expand the managed-origin set only after the canary meets
   routing-accuracy, reliability, latency, and privacy thresholds.

The audited live implementation starts in enforce mode. A new installation
should not copy that rollout decision without evidence from its own host.

## Does Claude need a restart?

No operating-system reboot is required. After installing or changing the hook
registration, start a fresh Claude Code session for deterministic activation
and verification. Hook scripts are command handlers invoked during events, but
settings reload behavior can vary by Claude Code version and surface. Do not
claim a running session reloaded the new policy without direct evidence.

## Rollback

1. Disable or remove the ladder's matcher entry from the applicable settings
   source. Preserve every unrelated hook.
2. Start a fresh Claude Code session and confirm subagent spawns no longer
   produce ladder decisions.
3. Restore the pre-install settings backup if the merge was incorrect.
4. Only after the registration is inactive, remove or archive the installed
   release directory.
5. Preserve the source release, manifest, validation results, and bounded
   decision evidence needed for the incident record.

## Common failure modes

| Symptom | Check |
| --- | --- |
| Hook never runs | Confirm the active settings location, matcher tool names, trust/policy restrictions, absolute command path, and fresh-session evidence. |
| Correct decision is logged but model is unchanged | Inspect actual `updatedInput`, `CLAUDE_CODE_SUBAGENT_MODEL`, agent frontmatter, `availableModels`, provider substitutions, and host-version behavior. |
| Every repository is gated | Verify origin normalization and the managed-origin allowlist. |
| Worktree is not recognized | Match canonical `origin`, not the worktree folder name. |
| Existing hooks disappeared | Restore the backup and merge arrays instead of replacing the `hooks` object. |
| Sensitive text appears in logs | Stop rollout, preserve evidence securely, fix the redaction schema, rotate the affected log, and rerun privacy tests. |
| Higher tiers are overused | Inspect blocker quality and the scenario corpus; do not weaken the ordered rule based only on subjective complaints. |

## Official references

- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
- [Claude Code settings](https://code.claude.com/docs/en/settings)
- [Claude Code subagents](https://code.claude.com/docs/en/sub-agents)
- [Claude Code model configuration](https://code.claude.com/docs/en/model-config)

These links describe the current product and will evolve. Record the exact
target version and verify its schema before enforcement.
