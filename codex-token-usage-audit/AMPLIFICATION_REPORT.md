# Passive amplification report

`generated_at_utc` records report generation, not a provider cutoff. Analytical
fields are reproducible for the same sealed inputs and source pins; the complete
JSON bytes differ by that timestamp. An optional baseline is sealed before use,
rechecked before return, and retained by digest/length in provenance. Changing a
rollout or baseline during generation rejects the report.

`amplification_report.py` is an explicitly invoked, body-free local diagnostic.
It does not alter retained rollouts, the normalized usage ledger, scheduler
configuration, or quota settings.

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
py -3 -B .\amplification_report.py --rollout C:\evidence\rollout.jsonl --task-class ordinary-engineering
```

The report reuses `codex_token_usage_audit.py` for token totals. Input includes
cached input, and reasoning output is already part of output; neither subset is
added to its parent total. It separately reports observed tool-result serialized
text-byte estimates and
hashed exact-repeat groups. Fingerprints include an explicit state qualifier when
the retained item provides one; otherwise state is reported as unknown. A repeat
is a review candidate, not evidence of waste.

An optional baseline is compared only when it is explicitly supplied, declares
`amplification-task-class-baseline-v1`, marks itself comparable, has the same
task class, and supplies finite supported `upper_bounds`. Bounds are caller
supplied rather than universal ceilings; the report emits observed rates and
excesses only against those bounds. The report makes no token-price,
subscription-debit, or savings claim. Lineage closure, external waits/wakes,
external I/O, and quota attribution
remain state-qualified unknowns unless an owning evidence source supplies them.
Observed wait calls cannot be labeled unchanged without a provider
state-transition receipt; intervention/rework and hook latency are unknown for
the same reason.

Output retains counters, bounded hashes, source-content digests, and parser/report
source pins only. Call and result figures are retained-record counts: durable IDs
support record correlation but never prove a tool execution, so the executed-call
count remains unknown. Inputs are sealed before parsing and rechecked before output;
a changed input rejects the report. It excludes prompt bodies, tool
arguments/results, paths, task/thread identifiers, raw token records, and price
data. No recurring automation or migration is created.
