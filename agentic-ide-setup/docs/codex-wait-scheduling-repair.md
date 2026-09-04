# Codex wait-scheduling repair brief

**Target:** `codex-workflow-hooks` (Azure DevOps: `rdprokes/adaptiveassetallocation/codex-workflow-hooks`)
**Audited build:** `0.6.3+sha.595cb759ab62a5039ce4e73f09edf41698ca59b0`, installed at
`%LOCALAPPDATA%\CodexWorkflowHooks\releases\0.6.3+sha.595cb759ab62a5039ce4e73f09edf41698ca59b0`
**Evidence window:** 2026-08-21 → 2026-09-03, 104,098 recorded events, 26,095 mutations
**Audited by:** Claude Opus 5, read-only, 2026-09-04

---

## Executive summary

The asynchronous wait/follow-up feature has **never executed once** since the evidence ledger
was created. Not "rarely" — zero times, across two weeks of heavy real delivery:

```
wait_obligations   0
wait_monitors      0
structured_waits   0
```

`cwh-wait` appears zero times in `~/.codex/.codex-global-state.json`, so no heartbeat automation
has ever been created either.

The design is sound. The read-back binding checks, the fail-closed timeouts, the
`human_approval_required` carve-out, and the intent classifier are all correct and worth keeping.
The feature is dead because of a **precondition it can never satisfy**, three layers upstream.

Meanwhile the guard half of the same runtime fires constantly, including 3,815 hard denials from
the same weak shell-context resolution that starves the ledger. One defect, two symptoms: the
half that says *no* fires nonstop, the half that says *keep watching* has never fired at all.

Do not treat this as a tuning problem. Fix the recorder, then prove the seam with a test that
does not inject its own precondition.

---

## Root cause chain

`detect_wait_obligation()` (`async_monitoring.py:65`) is gated on a `PUSHED` artifact whose digest
matches current `HEAD` and whose branch matches the current branch:

```python
pushed = ledger.latest_artifact(event.session_id, context, DeliveryState.PUSHED)
if not pushed:
    return None
```

The artifacts table contains exactly one state:

```
source_modified  3221   (source: apply_patch — every single row)
everything else     0
```

Zero `validated`. Zero `committed`. Zero `pushed`. Over the same window
`asset-allocation-jobs` alone took 254 commits and pushed a dozen `agent/*` branches. Real
delivery happened; the ledger observed none of it. So the wait trigger is not merely rare — it is
**transitively unreachable**.

Two gates upstream are responsible.

### Gate 1 — `explicit_success` discards unknown outcomes as failures

`hooks.py` (release line 488; source symbol `_post_bash`):

```python
explicit_success = exit_code == 0
...
if not explicit_success:
    write_log(data_dir, "post_tool", action_type=...)
    return None          # returns BEFORE any git commit/push detection
```

`parse_exit_code()` (`utils.py:171`) returns `None` when the provider's `tool_response` carries no
`exit_code` / `exitCode` / `returncode` / `status` key and no `Exit code: N` text. `None == 0` is
`False`, so an **unknown** outcome is treated identically to an **observed failure**.

Measured across all five runtime log files:

| post-tool class | with success flag | without |
| --- | ---: | ---: |
| `mutation` | 1,600 | 1,610 |
| `provider_write` (`az` / `gh` writes) | 0 | 581 |

Every single `provider_write` — which is exactly the `az repos pr create` / `az pipelines run`
class the monitor exists to watch — returns before reaching the detection code.

### Gate 2 — `single_segment` parses intent instead of observing state

`hooks.py` (release lines 596–628):

```python
single_segment = len(segments) == 1
if single_segment and re.search(r"\bgit(?:\s+-c\s+\S+|\s+-C\s+\S+)*\s+push\b", command, re.I) ...:
    ledger.mark_state(..., DeliveryState.PUSHED, digest=context.head, metadata={"branch": context.branch})
```

Any chained invocation — `git add -A && git commit -m ... && git push` — records nothing, because
the guess is made from command *text* rather than from repository *state*. This is the same class
of error the rest of the module carefully avoids: `_poll_pull_request` does a real read-back and
verifies four bindings before believing a status, but the state that feeds it is inferred from a
regex over a shell string.

### Downstream consequences

- All 687 `agentcoord` publications carry `material_transition: false`.
- `Stop` fired `stop_evidence_gap` **602 times** — the closeout guard correctly complaining, every
  session, for two weeks, that nothing reached a delivery state. That alarm was working. Nobody
  read it.

---

## Defects, in fix order

### D1 — Unknown command outcome is conflated with failure

**Where:** `hooks.py` `_post_bash`, release line 488 / 508–556; `utils.py:171` `parse_exit_code`.
**Symptom:** 581 of 581 `provider_write` events and half of all mutations skip delivery-state
recording entirely.
**Fix:** split the tri-state. `parse_exit_code` already distinguishes "absent" (`None`) from
"present and non-zero". Propagate that:

```python
observed_failure = exit_code is not None and exit_code != 0
if observed_failure:
    return None
```

Keep the existing `explicit_success` for anything that genuinely requires proof of success
(validation profiles). Delivery-state recording should proceed on *not-observed-failure* and rely
on state observation (D2) for correctness, because the observation itself is the proof.

**Acceptance:** a `PostToolUse` payload for `git push` with no `exit_code` key produces a `pushed`
artifact.

### D2 — Delivery state is inferred from command text, not observed

**Where:** `hooks.py` release lines 596–628 (`COMMITTED`, `PUSHED`) and 1725–1756.
**Symptom:** chained git commands record nothing; `single_segment` is load-bearing and shouldn't be.
**Fix:** after any git-bearing Bash mutation that was not an observed failure, *observe*:

- `git rev-parse HEAD` → compare against `repository_sessions.baseline_head` to detect a new commit.
- `git rev-parse @{u}` (or `git for-each-ref refs/remotes/<remote>/<branch>`) → when the upstream
  ref now equals `HEAD`, record `PUSHED` with that digest.

This removes the regex, removes `single_segment`, and makes the recorder correct for `&&` chains,
`git -C`, aliases, wrappers, and scripts. It also matches the standard the polling side already
holds itself to. Keep the `is_git_dry_run` suppression.

**Acceptance:** `git add -A && git commit -m x && git push` in one Bash call yields both a
`committed` and a `pushed` artifact with the correct digest and branch.

### D3 — The wait trigger fails silently when its precondition is missing

**Where:** `async_monitoring.py:53–110` `detect_wait_obligation`.
**Symptom:** `az repos pr create` succeeds, no `PUSHED` artifact exists, function returns `None`,
and nothing anywhere records that a monitorable operation was dropped on the floor. This is why the
outage lasted two weeks unnoticed.
**Fix:** D1 + D2 repair the precondition, but add the missing alarm regardless. When the command
matches a monitorable operation (`az repos pr create`, `az pipelines run`) and a resource id is
extractable, but no matching `PUSHED` artifact is found, emit a diagnostic event
`WAIT_TRIGGER_UNBOUND` with the operation kind and the reason (`no_pushed_artifact`,
`commit_mismatch`, `branch_mismatch`). Silent `return None` on a recognised operation is the bug
that hid every other bug here.

**Acceptance:** with the ledger deliberately empty, a successful `az repos pr create` writes one
`WAIT_TRIGGER_UNBOUND` event.

### D4 — The test suite injects the precondition it is supposed to exercise

**Where:** `tests/test_async_monitoring.py`, `setUp`, lines 77–80:

```python
self.ledger.mark_state(
    ...,
    DeliveryState.PUSHED,
    ...
)
```

**Symptom:** every wait-obligation test passes while the feature has never run in production. The
tests validate the *consumer* against a hand-placed precondition and never exercise the *producer*.
The seam between them — the only thing that was broken — is untested.
**Fix:** add an end-to-end test that never calls `mark_state` directly:

1. Drive `handle_event` with a real `PostToolUse` payload for `git push` (include a variant with no
   `exit_code` key, and a variant with the command chained behind `git commit`).
2. Assert a `pushed` artifact exists with the right digest and branch.
3. Drive `handle_event` with a `PostToolUse` payload for `az repos pr create`.
4. Assert a `wait_obligation` row appears.
5. Drive the `automation_update` registration and assert a `wait_monitor` row appears.

Any test that calls `mark_state(..., PUSHED, ...)` in setup is testing the half that was never
broken. Keep those, but they are not evidence the feature works.

### D5 — `SHELL_CONTEXT_OPAQUE` is a hard denial firing 3,815 times

**Where:** `guards.py:130, 142, 157, 717, 908, 931` — all `_deny(...)`, not warnings.
**Symptom:**

```
SHELL_CONTEXT_OPAQUE   3815   (hard deny)
GIT_DETACHED_HEAD      1415
SECRET_ENV_EXPANSION    260
FILESYSTEM_TARGET_UNKNOWN 138
MANAGED_REPO_UNREGISTERED  71
GIT_PROTECTED_REF_PUSH     18
```

**Assessment:** this is the same root cause family as D2 — the runtime cannot reliably resolve which
directory a shell command will actually execute in. A guard that denies 3,815 legitimate mutations
is not protecting the workflow, it is training agents to work around it. Fix cwd resolution
(`_git_effective_cwd`, `_shell_effective_cwd`) rather than loosening the denial.
**Fix:** resolve the effective cwd from the event envelope's `cwd` plus explicit `cd` / `git -C`
tokens, and treat an unresolvable *non-git* command as read-scoped rather than denied. Add a
per-reason-code counter to the runtime log so denial rates are visible without an ad-hoc query.

### D6 — Duplicate registration with conflicting rollout modes

**Where:** `%LOCALAPPDATA%\CodexWorkflowHooks\data\registrations.json`.

```json
{"repository_id": "codex-workflow-hooks", "git_common_dir": "C:\\Users\\rdpro\\OneDrive\\Documents\\ChatGPT\\codex-workflow-hooks\\.git", "rollout_mode": "shadow"}
{"repository_id": "codex-workflow-hooks", "git_common_dir": "C:\\Users\\rdpro\\Projects\\codex-workflow-hooks\\.git",                        "rollout_mode": "enforce"}
```

One `repository_id`, two checkouts, two conflicting rollout modes. Resolution is order-dependent.

Compounding it: the **OneDrive** checkout is the one that actually produced the installed build
(commit `595cb75`, "Merged PR 3043: Automate immutable main release tags", present on its
`origin/main`) — and it is registered in `shadow`. The **Projects** checkout is registered in
`enforce` but its working tree matches release **0.6.0**, and its `pyproject.toml` still says
`version = "0.6.0"` while the running build is `0.6.3`. So the enforcing registration points at a
tree that is three releases stale.

**Fix:** pick one canonical checkout, deregister the other, and make `hookctl` refuse to register a
second `git_common_dir` under an existing `repository_id` unless the conflict is explicitly resolved.

### D7 — No liveness signal on the feature itself

**Symptom:** a two-week total outage of a headline feature produced no alert. The single query
`SELECT COUNT(*) FROM wait_obligations` would have caught it on day one.
**Fix:** add to the `hookctl doctor` / diagnostics surface:

- counts for `wait_obligations`, `wait_monitors`, `structured_waits`
- artifact counts grouped by `state`, flagged when every row is `source_modified`
- `stop_evidence_gap` rate over the last 7 days
- denial counts by `reason_code`

Fail the check when a repository in `enforce` mode has recorded mutations but zero artifacts above
`source_modified` for more than 24 hours.

---

## Do not change

These are correct and the repair must preserve them:

- Read-back binding verification in `_poll_pull_request` / `_poll_pipeline` (repository id, source
  commit, source branch, protected target, pipeline definition). This is the strongest part of the
  design.
- `_PR_TIMEOUT_HOURS = 72`, `_PIPELINE_TIMEOUT_HOURS = 24`,
  `_GENERIC_WAIT_TIMEOUT_MINUTES = 60`, `_MONITOR_INTERVAL_MINUTES = 10`.
- `human_approval_required` on pending production approvals, and the rule that human approvals never
  auto-resume.
- `classify_delivery_intent` suppression for `no_wait` / `submit_only` / `local_only` /
  `planning_only`.
- The exact-contract validation in `register_monitor_from_tool` — the hook refusing to register a
  heartbeat whose prompt or rrule was altered is correct.
- Fail-closed behaviour for offline claims, registrations, and work starts.

---

## Reproduction

Run from any shell. `-X utf8` is required or non-ASCII output dies on the pipe.

```bash
python -X utf8 -c "
import sqlite3
p=r'C:\Users\rdpro\AppData\Local\CodexWorkflowHooks\data\evidence.sqlite3'
c=sqlite3.connect(p); q=lambda s: list(c.execute(s))
print('waits:', q('select (select count(*) from wait_obligations), (select count(*) from wait_monitors), (select count(*) from structured_waits)')[0])
print('artifacts:', q('select state, count(*) from repository_artifacts group by 1 order by 2 desc'))
print('window:', q('select min(recorded_at), max(recorded_at), count(*) from repository_events')[0])
print('denials:', q(\"select reason_code, count(*) from repository_events where reason_code like '%_%' and reason_code not like 'provider:%' group by 1 order by 2 desc limit 10\"))
"
```

Post-tool success-flag split:

```bash
python -X utf8 -c "
import json, glob, collections, os
d=r'C:\Users\rdpro\AppData\Local\CodexWorkflowHooks\data\logs'
c=collections.Counter()
for fn in glob.glob(os.path.join(d,'runtime.jsonl*')):
    for line in open(fn, encoding='utf-8', errors='replace'):
        try: r=json.loads(line)
        except Exception: continue
        if r.get('event')=='post_tool': c[(r.get('action_type'), r.get('success'))]+=1
for k,v in c.most_common(12): print(v,k)
"
```

Confirm no heartbeat has ever existed:

```bash
python -X utf8 -c "
s=open(r'C:\Users\rdpro\.codex\.codex-global-state.json', encoding='utf-8', errors='replace').read()
print('cwh-wait occurrences:', s.count('cwh-wait'))
"
```

---

## Validation plan

The unit suite passing is not evidence — it passed throughout the outage. Required proof, in order:

1. **Unit:** the new end-to-end tests from D4, including the no-`exit_code` and chained-command
   variants.
2. **Shadow:** install the repaired build in `shadow` mode on one registered repository. Run a real
   delivery: branch, edit, chained commit+push, `az repos pr create`. Then assert from the ledger
   that a `pushed` artifact, a `wait_obligation`, and a `wait_monitor` row all exist, and that the
   `[cwh-wait:...]` marker appears in `~/.codex/.codex-global-state.json`.
3. **Live poll:** let one heartbeat fire and confirm `poll-wait` returns a real Azure status. The
   read-back binding logic has never executed against a live resource — it is unproven code.
4. **Negative:** force a binding mismatch (retarget the PR, or force-push the source branch) and
   confirm `poll-wait` returns `binding_mismatch:` rather than a false success.
5. **Denial rate:** confirm `SHELL_CONTEXT_OPAQUE` denials drop substantially after D5, measured on
   the same counter added in D7.

Do not promote to `enforce` on any repository until step 3 has produced a real Azure read-back.

---

## What this brief does not cover

- The `agentcoord` bridge is healthy and out of scope here (`coord_doctor` returns `ok`, local
  outbox empty). Its client-side ergonomics are noted in the companion Claude document.
- `subagent_routing` has recorded 2 events in 104,098. Possibly correct, possibly a second dead
  path. Not investigated; audit separately rather than assuming either way.
