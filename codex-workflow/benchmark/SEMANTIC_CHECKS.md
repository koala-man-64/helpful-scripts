# Semantic evidence boundary

Eight fixed scenarios have deterministic evaluators. The runner retains the
workspace before and after dispatch, raw CLI events, the final answer, and exact
fixture references outside the model workspace. It verifies their hashes and
recomputes outcomes; model-supplied pass flags do not establish acceptance.

| Scenario | Recomputed evidence |
| --- | --- |
| Failed test | Narrow source change, preserved original test module, new cases that fail against original source and pass against the fix, fixed behavior assertions |
| Long output | Exact regenerated raw log, first BUILD417 failure, corrected route, preserved non-skipped tests |
| Missing dependency | Unchanged lock, supported stdlib result, preserved tests, observed commands contain no install |
| Wrong checkout | Observed initial HEAD, recovery to the pinned branch and target HEAD before mutation, exact final files and clean status |
| Cross-repository stale SHA | Independently observed upstream and downstream HEADs, recovery ordering, unchanged upstream, identical required contracts |
| Protected review | Exact proposed contracts and review summary, unchanged protected gate, explicit remaining human action |
| External research fixture | Unchanged pinned data, exact evidence values, independently calculated constraints and recommendation, snapshot limitation |
| Clarification and failure | Retained original failure, explicitly unknown target, dependent validation plan, one material target question, no deployment claim |

Localized evaluators run fixed Python commands in temporary fixture copies with
bytecode disabled and bounded execution time. Those copies are not an operating
system security sandbox. The trace allowlist is conservative: opaque commands,
unrecognized tools, mixed sessions and unfinished items reject the relevant
checks. Test discovery with skipped or expected-failure coverage is insufficient.

`semantic_preparation_pins()` supplies both the combined semantic implementation
digest and the hook verifier's `validator:deterministic-semantic-v1` entrypoint
pin, plus concrete fixture hashes. Each check artifact uses `benchmark-check-v1`,
binds the run, task, observation IDs, validator source and raw evidence references.
The semantic observation index links retained CLI bytes; it is not token usage
accounting or a complete attempt census.

The disabled bundle's source digest includes the evaluator implementation, but
the installed readiness verifier does not yet bind its expected combined and
entrypoint digests to that bundle authority or recompute the exact semantic
observation identity recipe. Those consumer-verifier joins remain promotion
blockers; a self-declared preparation pin is insufficient release authority.

Four host-event scenarios still lack implemented semantic evaluators in this
checkpoint: compaction/recovery, wait/resume, peer collaboration, and detached
review. The app-server raw capture and monetary calculator now have separate
diagnostic producers; their central acceptance and complete census remain unfinished.
Actual model execution, route admission,
candidate feature consumption, full accounting and the 72-run efficiency gate
remain unverified. No candidate is ready or activated.
