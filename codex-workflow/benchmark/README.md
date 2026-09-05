# Disabled candidate benchmark

This package prepares the schedule for **72 model tasks**: twelve fixed tasks, baseline and
candidate, three repetitions. Pair order is randomized with an explicit seed.
Warm and cold runs are different run sets. Offline unit tests and fixture checks
are not model executions and establish no efficiency result.

`manifest.py` fixes task prompts, scenarios and acceptance invariants.
`task_inputs/` contains named source files, deliberately failing unit tests and
external-service data; `fixed-inputs.json` pins their bytes. The long-line generator
produces exactly 153,000 Unicode characters. `prepare_fixture_scratch(path)` copies
these inputs to an explicit empty directory. The parent must provision task-owned
Git checkouts/base commits and actual host-event fixtures before model dispatch.
Compaction, protected review, resumable waits and children require real adapter
receipts; the supplied descriptions do not prove those events happened.

`prepare_run_set(pins=..., execution_mode="cold", seed=...)` fixes exact base
commits, dependency locks, skill/validator pins, external fixture hashes and both
variant configurations. `FrozenExecutionConfig` records actual model and effort;
unknown configuration can be prepared but cannot run or promote. Freeze the live
baseline at execution time. Never restore a historic model default to match an
older audit.

`CodexExecAdapter` uses the supported `codex exec --json` interface with an explicit
model and `model_reasoning_effort` override and sends the fixed prompt on stdin.
It revalidates the task worktree, HEAD, locks and skill pins before dispatch. Its
runtime preflight additionally requires the pinned absolute executable and an
immutable captured catalog advertising the requested model and effort. Its
owned process call uses `tools.output_projection.run_process`, preserving the real
exit status and raw combined output. It does not change CODEX_HOME, install global
instructions, disable hooks, bypass approvals, or start benchmark tasks implicitly.

Model and effort overrides are the directly implemented candidate mechanism.
Projection currently applies to the adapter's process output. That does **not**
prove projection of commands issued inside the model task. Instruction overlays,
compact context and hook features require a supported isolated benchmark mechanism
and evidence that each candidate run consumed the exact pinned inputs. Otherwise
leave those features unclaimed and unbenchmarked. A model-price improvement cannot
promote them. Host-hook feature IDs are `compact_contract_v1`,
`compact_routing_context_v1`, and `suppress_routine_context_v1` only when exercised.

Gate evaluation requires a trusted, pinned, deterministic validator registry.
Invariant validators inspect actual immutable proof artifacts; `accounting` verifies
the complete root/child/failed-attempt/review/recovery/clarification accounting.
Callbacks returning true in unit tests are explicitly synthetic test doubles, never
production acceptance evidence. A numeric metric or a caller-supplied pass flag is
insufficient. Missing validators, null accounting, unconsumed claimed features or
unfrozen configuration block promotion. Actual usage parsing and authoritative
accounting remain owned by `codex-workflow-hooks`; no second usage parser exists here.

The checked-in registry recomputes eight deterministic semantic evaluations from
the actual produced files and runner-retained evidence. Local checks execute
fixed behavior assertions and nonempty regression suites in temporary copies;
Git checks verify real HEADs, recovery order and preserved review boundaries;
research checks recompute the structured answers required by the fixed prompts.
Supplied pass flags cannot override these results. See [SEMANTIC_CHECKS.md](SEMANTIC_CHECKS.md).
The accounting diagnostic checks supplied totals through the hook-owned parser;
an independent complete-attempt census remains absent. Four host-event scenarios
are unimplemented in the CLI adapter. A separate version-pinned app-server raw
capture and request pricing derivation are implemented; neither establishes host
acceptance or complete accounting. See [CAPTURE_AND_PRICING.md](CAPTURE_AND_PRICING.md).
The [host census contract proposal](CENSUS_CONTRACT_PROPOSAL_V1.md) records the
required joins, host observations, partial states and separate verifier work.
The whole 72-run study is not ready for dispatch or promotion.

The candidate bundle retains its historical c540 installed-policy observation.
It does not attest the currently installed release. The coordinating owner will
freeze a new bundle after the final producer/verifier contract and installed
trust evidence are ready; intermediate installation changes do not rewrite the
fifteen preserved consumer locks.

## Runnable preparation and collection

Run these from `codex-workflow/`, using explicit caller-owned output paths:

```powershell
py -3 -B -m benchmark.runner capabilities
py -3 -B -m benchmark.runner prepare --pins C:\evidence\pins.json --mode cold --seed 7301 --output C:\evidence\prepared
py -3 -B -m benchmark.runner collect --run-set C:\evidence\prepared\run-set.json --run-id EXACT_PREPARED_RUN_ID --evidence C:\evidence\attempt-inputs.json --output C:\evidence\attempt-receipt.json
```

`pins.json` is the `PreparationPins.payload()` shape: `base_commits`,
`dependency_locks`, `skill_pins`, `external_fixtures`, both `variant_configs`
(`model`, `reasoning_effort`), and `claimed_features`. Preparation writes the exact
fixed schedule, concrete inputs, and capability report. It starts no model runs.

The explicit `CodexExecAdapter.dispatch` Python API starts one already-authorized
run only after its launch preflight. Persist its returned `codex-exec-dispatch-v1`
object as the dispatch artifact. It binds the complete request, preparation, and
raw event bytes. The supported CLI lifecycle events are documented in
[OpenAI's non-interactive mode guide](https://developers.openai.com/codex/noninteractive).

For semantic evidence use `runner.dispatch_observed(adapter, run, prepared, ...)`.
It requires an explicit `semantic-validators` implementation hash in `skill_pins`
and the task's `fixed-inputs.json` digest in `external_fixtures`. It captures before
and after workspace observations, final response and raw references outside the
model workspace. Each attempt requires its own empty raw directory. `collect
--semantic` runs the evaluators and records failures without discarding the raw
attempt. The supplied dispatch artifact discovers the retained reference set;
replaced bytes or conflicting supplied paths fail verification.

`attempt-inputs.json` supplies `raw_artifacts` paths named `dispatch`, `events`,
`usage_observations`, and `measurements`, plus optional `invariant_evidence`,
`accounting_totals`, `failure_artifacts` (raw artifact names), `defects`, and
`feature_consumption`. `collect_receipt` joins the actual CLI session identity to
the prepared run and the hook-owned `benchmark-measurements-v1` task scope. It
preserves failed executions, null accounting, and absent acceptance as such;
`structural_errors` and `acceptance_verified:false` accompany the collected
receipt. A collected receipt does not establish complete accounting. Root/child
and failed-attempt census, compaction/wait/peer/review producers,
and acceptance of separate monetary derivations still need their authoritative implementations.

The gate applies all declared acceptance/safety/continuation/compaction checks,
aggregate cost **per accepted task**, paired median cost, and the specified cohort
median/p90 limits. All attempt costs remain in the numerator. Cache and reasoning
subsets are never added again; the accounting validator must choose request or
cumulative accounting explicitly. The separate published Codex-equivalent
estimate does not establish subscription charges. An unavailable USD basis stays null.

`emit_artifacts(output, prepared=..., receipts=..., validators=..., observations=...)`
writes an explicit artifact set. It includes the fixed definition/thresholds,
prepared run set, all 72 original receipts, recomputed results payload and gate,
public receipt, observation index and per-run raw/acceptance references. The
`results_payload` digest uses sorted compact JSON with default ASCII escaping.
`load_verified_artifacts(path, validators=...)` verifies bytes, reconstructs the
exact schedule and recomputes acceptance and gate results. Production activation
additionally needs the hook-owned usage/measurement/check verification and five
real consumer validation receipts. No install or promotion action exists here.

Validation commands (offline):

```powershell
py -3 -B -m unittest discover -s codex-workflow/tests -p 'test_benchmark*.py' -v
```

No production model defaults, installed workflow settings, external consumers or
business dependencies are modified. Rollback is to stop using the opt-in candidate
helpers and reject its bundle; the current installed workflow remains available.
