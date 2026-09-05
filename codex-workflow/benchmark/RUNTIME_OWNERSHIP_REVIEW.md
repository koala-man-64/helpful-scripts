# Runtime ownership source review

Scope: opt-in local usage extraction, benchmark evidence capture/validation,
separate request-price derivation, disabled candidate rendering, and the existing
explicit operator bytecode recovery helper. This is source review evidence;
it does not establish release, installation, live route admission or an executed
benchmark.

| Capability | Owner and boundary |
| --- | --- |
| Local rollout/optional SQLite reads | Audit extractor; SQLite uses `mode=ro` and `PRAGMA query_only = ON`, reports read failures and closes the connection |
| App-server process and raw files | Benchmark caller; exact binary/protocol preflight, caller-owned workspace and new raw files outside it; bounded shutdown with explicit failures |
| Model dispatch and immutable preparation | Prepared benchmark owner; runtime and model-catalog digests must match preparation pins before dispatch; no automatic runtime install or approval answers |
| Usage validation and acceptance | Central hooks owner; the producer consumes its pinned source API and never treats a local estimate or snapshot as complete accounting |
| Sealed host diagnostics | Benchmark caller; reads retained streams and validates the pinned provider projection, writes a new body-free diagnostic, and leaves admission and closure false |
| Candidate source and lock rendering | Source delivery owner; disabled outputs and historical policy observation; no installed configuration or consumer writes |
| Installed release recovery | Runtime owner/operator; `Repair-CodexHookBytecode.ps1` requires an explicit invocation, exact diagnosed release/cache inventory and `-Apply`; preserves quarantined bytes and verifies the installed release afterward |

Source scanner command (enforcement mode, bytecode disabled):

```powershell
py -3 -B .codex/skills/runtime-ownership-enforcer/scripts/scan_runtime_ownership.py . --runtime-dir codex-workflow/benchmark --runtime-dir codex-workflow/tools --runtime-dir codex-token-usage-audit --format text
```

The 2026-09-05 host-diagnostic scan returned exit 1 with 30 pattern matches. It did **not** pass.
Inspection classified the matches as follows; no scanner exceptions or policy
changes were introduced to alter that result.

| Matches | Inspected execution path | Manual classification |
| --- | --- | --- |
| 3 `masked_environment_error` | `load_state_metadata` reads parent/child rows; its `sqlite3.Error` handler adds a warning and `finally` closes the read-only connection | `valid_runtime_data_operation`; no repair or concealed success |
| 7 `runtime_bootstrap` | App-server JSON-RPC `initialize` request/response checks | `valid_runtime_data_operation`; protocol handshake, no infrastructure provisioning |
| 13 `runtime_bootstrap` | Deterministic schedule `seed` arguments and cumulative-counter explanation | `valid_runtime_data_operation`; statistical/data processing terminology, no environment mutation |
| 2 `runtime_bootstrap` | Desktop token-vector reconciliation failure messages | `valid_runtime_data_operation`; failed validation is reported, with no infrastructure repair |
| 2 `masked_environment_error` | `host_protocol._load_projection` converts malformed JSON into an explicit validation failure | `valid_runtime_data_operation`; no fallback schema or concealed success |
| 3 `masked_environment_error` | `extract_host_protocol.extract_projection` rejects malformed provider JSON | `valid_deployment_provisioning_operation`; explicit source-generation tooling, no runtime repair |

The explicit recovery helper is a
`valid_deployment_provisioning_operation` in the management plane. The benchmark
does not invoke it as serving-runtime recovery. This task did not apply it to the
currently installed release.

Focused regressions verify unknown/mismatched runtime rejection, immutable pins,
async errors, shutdown timeouts, preserved raw files and incomplete metadata.
Pure pricing verifies cached/reasoning subsets and leaves unknown request
configuration unpriced. Actual provider execution and full accounting remain
unverified; synthetic tests do not stand in for them.

**runtime-ownership-enforcer: blocker for study activation.** Source ownership
review is complete, with no substantiated cross-plane runtime mutation in the
reviewed paths. The lifecycle release/runtime gates still lack a complete
attempt/terminal census, an authoritative external-wait continuation receipt,
four implemented host-semantic checks and the actual 72-run study. No passing
production lifecycle manifest is available and no production lifecycle pass is
claimed. The central owner retains release/admission ownership; the provider must
expose the missing closure and scheduler evidence described in
[the census proposal](CENSUS_CONTRACT_PROPOSAL_V1.md).

Rollback for these opt-in source helpers is to stop invoking them and reject the
candidate bundle. Installed settings are independently owned and are not changed
by that rollback.
