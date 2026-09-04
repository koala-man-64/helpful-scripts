# Bringing wait-scheduling and peer coordination to Claude Code

**Companion to:** [Codex wait-scheduling repair brief](codex-wait-scheduling-repair.md)
**Audited:** Claude Code 2.1.236 on this machine, 2026-09-04
**Scope:** what already works, what is genuinely missing, and how to build the gap without
reproducing the Codex failure mode.

---

## The headline

**Claude already has more scheduling primitives than Codex does, and the peer-coordination half
works better today than the Codex equivalent.** This is not a port. Roughly 70% of the capability
is present and unused; the remaining 30% is one hook and one small durable registry.

The single most important design constraint comes from the Codex post-mortem: **Codex's follow-up
scheduling died because its trigger depended on a self-maintained model of repository delivery
state that drifted to empty and stayed there.** Claude's scheduling primitives fire on harness
state — a process exited, a wall-clock time arrived, a stdout line appeared — not on inferred repo
state. Keep it that way. Do not port the `DeliveryState` ladder.

---

## Implementation status

Steps 1-5 of the build order are implemented and live in `~/.claude/hooks`, mirrored into
`profile/claude/hooks` for the portable installer.

| Component | File | State |
| --- | --- | --- |
| Durable wait registry + timeouts + liveness `doctor` | `wait_registry.py` | done |
| `PostToolUse` detector | `post_tool_use_wait_detector.py` | done, registered on `Bash\|PowerShell` |
| `SessionStart` surfacing of outstanding waits | `session_start_team_context.py` | done |
| Read-back polling with binding verification | `wait_poll.py` | done, Azure DevOps PR + pipeline, GitHub PR |
| Tests | `test_wait_registry.py` | 33 tests |

Verified against the live GitHub API, not only in unit tests: a wait whose recorded branch and
commit no longer match resolved to `failed: binding_mismatch:source_branch,source_commit` even
though the pull request really was merged, and a correctly bound wait for the same PR resolved to
`succeeded: pr_merged`. Refusing to confirm a merged PR that is not the one the wait was
registered for is the safety property the whole read-back design exists for, and it is the
validation the Codex implementation never obtained.

Remaining from the build order: step 5 arming via scheduled tasks is documented but not wired into
a helper, step 6 `gh pr` support is present in the poller and detector but only PR create is
detected, and step 7 agentcoord lifecycle hooks for Claude are not installed.

Registry format is `~/.claude/waits/registry.json`; override with `CLAUDE_WAITS_PATH`.
Check liveness with `py "%USERPROFILE%\.claude\hooks\wait_poll.py" doctor`.

## What Claude already has (verified)

| Facility | Kind | Durability | What it is for |
| --- | --- | --- | --- |
| `Bash(run_in_background: true)` | one completion notification | session | "tell me when this finishes" — the command exits, you are re-invoked |
| `Monitor` | one notification per stdout line | session (`persistent: true` = session-length) | "tell me each time X happens" — log tails, poll loops, WebSocket frames |
| `ScheduleWakeup` | self-paced re-invocation | session, `/loop` dynamic mode only | agent picks its own next check-in, 60–3600s |
| `CronCreate` / `CronList` / `CronDelete` | cron-fired prompt | **session-only, in-memory, 7-day auto-expiry** | recurring prompts within one session; `durable` param has no effect |
| `mcp__scheduled-tasks__create_scheduled_task` | cron or one-shot `fireAt` | **durable — `~/.claude/scheduled-tasks/{taskId}/SKILL.md`** | survives session end; runs while the app is open, or on next launch if missed |
| `PushNotification` | push to the user's device | — | escalate an event that changes what they'd do next |
| `TaskStop` / `TaskOutput` | control | session | cancel or read a background task |

Peer coordination:

| Facility | What it gives you | Verified state |
| --- | --- | --- |
| `ListAgents` | every peer session by name, kind (`interactive` / `cloud` / `Remote Control`), and status | **working** — returned 4 peers live during the audit |
| `SendMessage` | direct message to a named peer session | harness-dependent; present at session start, withdrawn mid-session during the audit |
| `mcp__ccd_session_mgmt__list_sessions` | other CCD sessions with title, branch, PR, sidebar group | available |
| `mcp__ccd_session_mgmt__search_session_transcripts` | search across past session transcripts | available — no Codex equivalent |
| `mcp__ccd_session_mgmt__send_message` | message another CCD session | available |
| `mcp__agentcoord__*` (21 tools) | the same Postgres-backed cross-agent bus Codex uses | `coord_doctor` returns `ok`, local outbox empty |

Hooks currently installed in `~/.claude/settings.json`:

```
SessionStart   (startup|resume|compact)  session_start_team_context.py
UserPromptSubmit                          user_prompt_submit_router.py
PreToolUse     (Bash|PowerShell)          pre_tool_use_bash_guard.py
PreToolUse     (Agent|Task)               pre_tool_use_agent_ladder.py
Stop                                      stop_team_closeout.py, stop_gateway_bookkeeper_recap.py
```

**There is no `PostToolUse` hook and no `SessionEnd` hook.** That is the whole gap.

---

## Codex → Claude capability map

| Codex mechanism | Claude equivalent | Status |
| --- | --- | --- |
| `automation_update` heartbeat, `FREQ=MINUTELY;INTERVAL=10`, thread-attached | `mcp__scheduled-tasks__create_scheduled_task` with `cronExpression` | present, durable, **better** — survives the session |
| `poll-wait` re-entering the thread every 10 min | scheduled-task prompt, or `Monitor` with a poll loop | present |
| `_GENERIC_WAIT_TIMEOUT_MINUTES = 60` | `Monitor(timeout_ms)`, or a `fireAt` one-shot | present |
| Wait obligation registry (`wait_obligations` table) | — | **missing** |
| `detect_wait_obligation` on `PostToolUse` | — | **missing (no PostToolUse hook)** |
| Resumed session learns of outstanding waits | — | **missing** |
| `coord_who_is_working` | `ListAgents` | present and working |
| `coord_send_message` | `SendMessage` / `ccd_session_mgmt__send_message` / `agentcoord` | present |
| `DeliveryState` evidence ladder | — | **deliberately not wanted — see below** |

---

## The design rule

Codex's chain was:

```
Bash command text → regex → inferred delivery state → ledger row → precondition check → trigger
```

Six links, each lossy, and the whole thing hung off `len(segments) == 1`. It broke at link three
and stayed broken for two weeks because nothing asserted the chain was intact.

Claude's chain should be:

```
Bash command just run → detect monitorable operation → register wait → schedule check
```

Three links, and link one is the literal command the harness just executed, not a reconstruction.

Concretely: **trigger on the operation, not on a modelled precondition.** Codex refuses to register
a monitor unless it can first prove a matching `PUSHED` artifact exists. That gate is what killed
it. Claude should register the wait when it sees `az repos pr create` succeed, and let the *poll*
verify bindings — which is exactly what Codex's polling side already does correctly. Verification
belongs at read-back time, where you can compare against the live resource, not at registration
time against a self-maintained cache.

If the binding turns out wrong, the poll reports `binding_mismatch` and the wait fails closed. That
is strictly better than never registering.

---

## What to build

### 1. `post_tool_use_wait_detector.py` (new `PostToolUse` hook)

Matcher: `Bash|PowerShell`.

Detect, from the command that just ran and its output:

| Pattern | Resource id | Poll command |
| --- | --- | --- |
| `az repos pr create` | `pullRequestId` | `az repos pr show --id N` |
| `az pipelines run` | `id` / `buildId` | `az pipelines runs show --id N` |
| `gh pr create` | PR number from URL | `gh pr checks N` / `gh pr view N` |

On a match, append a row to the registry (below) and return `additionalContext` telling the agent a
wait is outstanding and how to arm it. Do **not** try to infer whether a push happened first —
record `head` and `branch` from `git rev-parse HEAD` / `--abbrev-ref HEAD` as *binding metadata to
verify later*, not as a precondition to check now.

Take the tri-state lesson from the Codex brief: treat a missing exit code as **unknown**, not
failure. Register the wait; let the poll sort it out.

### 2. `~/.claude/waits/registry.json` — durable wait registry

One small JSON file. Fields, borrowed directly from the Codex schema because that part is right:

```
wait_id, provider, operation_kind, resource_id, repository, branch, commit,
target_state, status, detail_code, created_at, last_checked_at, session_id
```

Statuses: `registered → pending → succeeded | failed | timed_out | human_approval_required`.

Rules to carry over verbatim from Codex:

- PR timeout 72h, pipeline timeout 24h, generic wait 60 min.
- A pending production approval resolves to `human_approval_required` and **never** auto-resumes.
- Verify repository id, source commit, source branch, and protected target on every poll before
  believing a status.

Rules to *not* carry over: the `PUSHED` precondition, and any state ladder above what the registry
row already holds.

### 3. Arming the check

Pick per expected duration:

- **Minutes, session stays open** — `Monitor` with a poll loop, `persistent: true`. Emit on every
  terminal status (`succeeded|failed|cancelled|timeout`), never only on success; a monitor that
  greps for success alone is silent through a crashloop, and silence looks identical to "still
  running."
- **Hours, or the session may close** — `mcp__scheduled-tasks__create_scheduled_task`. Each run
  starts fresh with **no memory of the conversation**, so the prompt must be fully self-contained.
  Codex's `monitor_contract()` prompt is already written that way and ports almost verbatim: the
  marker, the poll instruction, the "unchanged and pending → stay concise and keep waiting"
  behaviour, the explicit stop conditions, and the "never approve a protected gate, never treat
  monitor registration as delivery evidence" clause.
- **Not `CronCreate`** for anything that must outlive the session — it is in-memory only and
  auto-expires after 7 days, whatever `durable` is set to.

### 4. `SessionStart` addition — surface outstanding waits

Extend `session_start_team_context.py` to read the registry and inject any non-terminal waits into
the session context. This is the piece that makes waits survive a resume, and Codex has no
equivalent — its heartbeats are thread-attached and die with the thread.

### 5. Liveness check — build this at the same time, not later

The Codex outage lasted two weeks because nothing asserted the feature had ever run. Add a check
that reports:

- count of registry rows by status
- count of waits registered in the last 7 days
- any wait in `pending` past its timeout
- whether a monitorable operation was detected but no wait was registered

and have it fail loudly when monitorable operations are being run but zero waits exist. **Ship this
in the same change as the detector.** A feature with no liveness signal is a feature you will
discover is dead a fortnight late.

---

## Peer coordination: mostly already done

`ListAgents` answers "what are my other agents doing" today, with session kind and status, and it
works. `search_session_transcripts` goes further than anything Codex has — it searches across past
sessions, not just live ones. `agentcoord` is wired in and healthy.

Two real gaps:

1. **`coord_status`, `coord_list_participants`, and `coord_who_is_working` fail from an unregistered
   client** with a bare `Error executing tool`. Failing closed is correct; failing opaquely on the
   three tools that answer the peer-visibility question is not. Either auto-register on first use,
   or return a message naming `coord_register` as the fix. This is a one-line ergonomics fix in
   `agentcoord`'s MCP layer and it benefits Codex identically.

2. **Nothing registers Claude sessions on the bus automatically.** Codex has `SessionStart` /
   `Stop` / `SessionEnd` agentcoord hooks in `~/.codex/hooks.json`; Claude has none. If cross-tool
   visibility matters, install the equivalent — `agentcoord` ships a Claude Code hook installer and
   the documented identity scheme (`claude:rudy-primary:root`) already anticipates it. Use a
   distinct `AGENTCOORD_STATE_PATH` (`%LOCALAPPDATA%\agentcoord\claude-primary.db`) so the Claude
   session record cannot restore the Codex one's token.

---

## Non-goals

- **Do not build an evidence ledger.** Codex needs one because it also does mutation governance;
  wait-scheduling does not require it, and coupling the two is precisely what broke Codex.
- **Do not infer delivery state from command text.** If you ever need repository state, observe it
  (`git rev-parse HEAD`, `git rev-parse @{u}`) at the moment you need it.
- **Do not gate registration on a precondition you maintain yourself.** Verify at read-back.
- **Do not have Claude approve production gates.** Same carve-out as Codex: queue the run, read the
  status, leave the approval to Rudy, report it as the remaining blocker.

---

## Suggested build order

1. Registry file format + the liveness check (small, and it makes everything after it observable).
2. `PostToolUse` detector for `az repos pr create` and `az pipelines run`.
3. `SessionStart` surfacing of outstanding waits.
4. Poll implementation with the four binding checks, reusing Codex's `_poll_pull_request` logic —
   that code is correct, it has simply never run against a live resource.
5. Scheduled-task arming for long waits.
6. `gh pr` support.
7. `agentcoord` Claude lifecycle hooks, if cross-tool visibility is wanted.

Steps 1–3 are the minimum that produces working follow-up. Step 4 is where the Codex read-back
logic finally gets exercised — worth doing carefully, and worth reporting back to the Codex repair,
since a working implementation on the Claude side is the best available proof that its polling
design is sound.
