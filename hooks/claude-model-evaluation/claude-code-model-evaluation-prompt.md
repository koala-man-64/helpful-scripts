# Claude Code implementation handoff

Give Claude access to this entire folder, then paste the prompt below. Run it
from the repository that should own the versioned source. The prompt instructs
Claude to build and validate the implementation; it does not treat the live
user profile as authoritative source.

## Prompt

```text
Implement a tracked, portable Claude Code subagent model-evaluation ladder for
the current environment.

Authoritative requirements

- Within the accessible checkout, locate exactly one file named
  `claude-code-model-evaluation-prompt.md`. If zero or multiple matches exist,
  stop and report the ambiguity without changing anything.
- Read that file's sibling `README.md` completely. Treat it as the behavioral,
  rollout, privacy, validation, and rollback specification for this task.
- Verify every host-specific detail against the current official Claude Code
  hook, settings, subagent, and model documentation before editing.
- Record `claude --version`, platform, provider, active Claude configuration
  directory, settings sources, model restrictions, and actual subagent tool
  names. Do not infer them from another machine.

Locked model policy

- The preference and escalation order is exactly Haiku -> Sonnet -> Opus.
- Haiku is the default leaf tier and requires no lower-tier blocker.
- Sonnet requires one concrete Haiku blocker.
- Opus requires concrete Haiku and Sonnet blockers.
- Reject invalid or under-justified contracts. Never silently promote a task.
- A failed lower-tier attempt may justify a new parent-authored higher-tier
  contract, but must not trigger automatic retry or automatic promotion.
- Keep Fable and every other model outside this requested three-tier policy.
  Adding one requires a separately designed and evaluated policy.
- Use the target host's supported model aliases or pinned IDs, and record the
  resolved model versions during user-path validation.

Scope and ownership

- Classify this as local workflow-governance unless the target repository's
  own instructions prove otherwise. Do not change application APIs, schemas,
  serialization, databases, or production infrastructure.
- Establish one version-controlled source directory named
  `claude-agent-ladder` in the owning repository. Do not make
  `~/.claude/hooks` the only source of truth.
- Before editing, follow the repository's branch, tracking, review, and finish
  workflow. Preserve all unrelated dirty files and settings.
- If the owning repository, install scope, managed repository set, or allowed
  live-edit authority cannot be discovered safely, ask at most three targeted
  questions. Do not guess across user profiles or machines.
- Work only in the current operating environment. Windows, WSL, containers,
  remote hosts, CI workers, and other user profiles have separate Claude homes.

Required source package

Create or update this bounded package:

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

If one platform is unavailable, implement and test the current platform's
installer and mark the other installer `Unverified / Needs confirmation`
instead of inventing results.

Policy implementation

1. Define the ordered tuple `haiku`, `sonnet`, `opus` and an explicit mapping
   from each tier to its model alias or pinned model ID.
2. Define a versioned leading JSON envelope named
   `<claude_subagent_task_v1>`. Require:
   - tier;
   - one precise objective;
   - non-empty scope;
   - non-empty acceptance checks;
   - constraints;
   - no unresolved dependencies;
   - `decomposition_attempted: true`;
   - a non-empty blocker for every lower tier.
3. Reject malformed/missing envelopes, unknown tiers, skipped blockers,
   unresolved dependencies, explicit-model conflicts, implicit/full-history
   forks, and structurally writable agents used for declared read-only work.
4. Anchor the envelope at the start of the prompt and strip it before the
   worker receives the task.
5. Use stable ASCII reason codes. Keep Unicode out of executable hook logic,
   reason codes, and machine-facing output.
6. Never infer promotion from task prose or from a model's confident output.

Hook implementation

- Discover and test the target version's actual subagent tool names. Do not
  copy a matcher from another host.
- Use a `PreToolUse` command hook scoped to those tools.
- Read the event JSON from stdin and treat `tool_input` as untrusted.
- Outside the configured managed scope, emit nothing and exit successfully.
- In managed scope, fail closed on invalid contracts with the current
  `hookSpecificOutput.permissionDecision: "deny"` schema and an actionable
  reason containing the stable code.
- For a valid contract, clone the entire tool input, change only `model`, strip
  the leading envelope from `prompt`, and return the complete replacement under
  `hookSpecificOutput.updatedInput`.
- Treat the permission decision as a versioned security boundary. Start a
  canary with `permissionDecision: "ask"` when the current schema requires a
  decision, so the modified call is shown for confirmation. Never add `allow`
  merely to make rewriting work because it auto-approves the call. If the
  installed version supports omission while preserving the normal permission
  flow, use it only after an exact-version live test. Block enforcement until
  the rewrite and intended permission behavior are both proven on the host.
- Optionally inject concise SessionStart guidance, but only in managed scope.
  Guidance is explanatory; the deterministic hook is authoritative.

Security and provenance

- Treat command hooks as executable code running with the user's privileges.
- Install only a reviewed, versioned release from the tracked owning source.
- Do not treat a digest generated from the same untrusted checkout as proof of
  provenance. Prefer a signed release or independently supplied expected
  digest when available, and use immutable release identifiers.
- Use absolute command paths, least-privilege file permissions, and a dedicated
  bounded log directory. Do not inspect unrelated files or record environment
  values, prompt text, task text, credentials, or other sensitive content.
- If an installed hook or merged settings file differs from the reviewed
  manifest, disable this hook's matcher first, preserve evidence, and treat the
  mismatch as an incident until a trusted release is restored.

Repository scope

- Make the managed repository set explicit configuration.
- Identify repositories by normalized canonical origin, not folder or worktree
  name.
- Normalize HTTPS, SSH, case, and `.git` suffixes in tests.
- Make an absent/unrecognized origin a no-op unless the target's documented
  policy explicitly requires a fail-closed alternative.
- Start with one canary repository. Do not copy repository identities from the
  sibling guide without confirming ownership and scope.

Model precedence and capability checks

- Inspect `CLAUDE_CODE_SUBAGENT_MODEL`; it takes precedence over a
  per-invocation model. Stop or explicitly reconcile it before enforcement.
- Inspect `availableModels`, organization restrictions, provider substitutions,
  subagent frontmatter, and the session model.
- Keep reasoning effort, tools, permissions, and max turns separate from model
  routing. If tier-specific constraints are required, implement tested custom
  agent definitions rather than claiming the hook controls unsupported fields.

Privacy and evidence

- Write a bounded JSONL decision log containing only timestamp, repository
  identifier, decision, tier, selected model when routed, selection source,
  subagent type, stable reason code, and policy/release version.
- Never persist prompt text, objective, scope, acceptance checks, constraints,
  source code, tool output, authentication material, full local paths, or
  runtime environment values.
- Add an explicit size cap or rotation policy and privacy regression tests.
- Keep source, release, installation, hook-decision, and actual-model evidence
  distinct in all reporting.

Installer requirements

- Resolve the active Claude configuration directory from the target
  environment; do not assume one user's absolute path.
- Support dry-run, install, check, idempotent second install, and rollback.
- Back up every non-empty settings file before modifying it and report the
  backup path without dumping its contents.
- Install into a versioned release directory, generate and verify SHA-256
  manifest entries, and keep installer provenance.
- Merge the new matcher group into existing hook arrays. Never replace the
  complete `hooks` object or remove unrelated handlers.
- Treat user settings, project settings, local settings, managed policy, and
  plugin hooks as additive sources that must be inventoried for conflicts.
- Do not install or enable enforce mode until source tests pass.

Tests

Create deterministic unit tests for at least:

- all three tier mappings;
- Haiku with no blockers;
- Sonnet with and without the Haiku blocker;
- Opus with both blockers and with either blocker missing;
- blank blockers and unknown tiers;
- missing, malformed, non-object, and non-leading envelopes;
- empty scope and acceptance checks;
- decomposition not attested;
- unresolved dependencies;
- implicit/full-history forks;
- matching and conflicting explicit models;
- read-only agent boundaries;
- preservation of unrelated tool-input fields;
- envelope stripping;
- no silent promotion;
- managed, unmanaged, missing-origin, HTTPS, SSH, case, and `.git` identities;
- non-subagent tools;
- redacted and bounded logs;
- logging failure behavior;
- installer dry-run, hash verification, idempotence, and rollback.

Offline model-output evaluation

- Keep output evaluation outside the hook event path.
- Create a versioned, privacy-safe scenario format with executable or explicit
  acceptance checks and an expected lowest passing tier.
- Include scenarios for durable fix versus flashy demo, retry without novelty,
  disproportionate testing, unsupported completion claims, unnecessary
  cleanup, mixed tier signals, and justified promotions after recorded lower-
  tier failures.
- Run Haiku first, Sonnet only after a recorded Haiku failure, and Opus only
  after a recorded Sonnet failure. For comparative benchmarks, run each model
  independently on identical inputs.
- Record pass/fail, resolved model ID, verification, rework, promotion, latency,
  usage, and cost. Do not use self-reported confidence as correctness.

Rollout and live proof

1. Shadow: evaluate and log recommendations without denial or rewrite.
2. Canary: enforce in one low-risk managed repository with rollback criteria.
3. Enforce: expand only after explicit routing-accuracy, reliability, latency,
   and privacy thresholds pass.

For the canary, prove with real Claude Code subagent calls:

- valid Haiku is actually executed by Haiku;
- invalid Sonnet is denied;
- valid Sonnet is actually executed by Sonnet;
- valid Opus with both blockers is actually executed by Opus;
- unmanaged repositories are untouched;
- logs contain no task text;
- model environment variables, allowlists, and provider substitution did not
  silently change the selected model.

No operating-system reboot is required. Start a fresh Claude Code session after
installing or changing hook registration for deterministic activation proof.
If authenticated live checks cannot run, report them as
`Unverified / Needs confirmation`; do not infer runtime behavior from files,
unit tests, logs written by the hook, a commit, or an installation check.

Required final report

- target platform, Claude Code version, provider, and configuration directory;
- contract classification and owning source repository;
- branch/work item/PR evidence required by that repository;
- managed repository set and canary choice;
- source version, manifest digest, installed release path, and backup paths;
- files created or changed and why;
- exact unit, installer, privacy, and static validation results;
- shadow/canary/enforce state;
- real Haiku, Sonnet, and Opus user-path results reported independently;
- model precedence or substitution risks;
- restart/session action taken;
- rollback command and any remaining unverified evidence.
```

## Expected outcome

Claude should produce a tracked and testable source package, preserve unrelated
configuration, and install only after source validation. The first enforced
scope should be one canary repository. A hook decision record is not proof that
the requested model actually executed the task.
