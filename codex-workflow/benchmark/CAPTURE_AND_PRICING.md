# Capture and pricing evidence

## Runtime preflight

`CodexExecAdapter` requires an explicit `RuntimePin` and a content-addressed
`model_catalog_ref` before any dispatch. Their digests must match the frozen
`PreparationPins.skill_pins` entries `codex-runtime` and `codex-model-catalog`.
The preflight verifies the executable's
SHA-256 and version, then checks that the captured complete catalog names the
same binary and advertises the requested model and effort. It uses that absolute
binary path for the CLI invocation. Catalog defaults do not establish the user's
selected configuration, service tier, actual child admission, or execution.

The supported runtime snapshot is `codex-cli 0.153.4`, executable SHA-256
`a1cf6360ca71918d5466bc3a32d9f18b7044c9128756d1949e715d277b88c9b6`.
The protocol pin is the **combined v1 and v2 export** named
`codex_app_server_protocol.schemas.json`, SHA-256
`e8284c5cb8157554a3dd1e035aadbd4325aea501af56887e9c2e12eb1b9b9448`.
The separate `.v2.schemas.json` file has different bytes and is not this pin.
The prior 0.153.1 executable (SHA-256
`56a84de2b617af6b95b0c5c5d8ae120d3c2fb69008ab330c7e7df3945b98b782`)
became unavailable after the 2026-09-05 desktop update. The deliberate 0.153.4
refresh verified the new binary and a complete captured catalog, digest
`sha256:71053d006b804e42677e5b4bb52ff18f700259d90efa6bb7df5ed0357e8d60ae`.
Its combined protocol export is byte-identical to the historical 0.153.1 export.
Dispatch still requires the exact runtime/catalog to be reverified; source
versions and the actual selected baseline are not inferred from catalog defaults.

## Passive app-server capture

`AppServerCapture.start()` requires explicit run ID, workspace, model and effort,
the runtime pin, protocol version and exact exported schema file. It starts the
supported stdio endpoint in that workspace. It does not install anything, change
CODEX_HOME, answer server approval requests, or weaken normal policy.

Initialization must return the actual 0.153.4 fields (`codexHome`,
`platformFamily`, `platformOs`, `userAgent`) before the initialized notification
and subsequent requests. The narrow request surface supports model catalog,
thread start/read, turn start/steer, compaction and review. Raw incoming/outgoing
JSON-RPC and stderr are retained outside the model workspace. The process and
streams are owned by the capture; `close()` must complete before metadata is
frozen. A bounded event queue continues preserving raw output if event delivery
overflows, and records incomplete evidence.
Asynchronous error/retry notifications also mark the census partial. Shutdown
errors retain their diagnostics, prevent artifact metadata from being frozen,
and permit an explicit cleanup retry; a still-active reader keeps its owned
handles until it stops so closing a buffered pipe cannot deadlock on that reader.

`codex-app-server-capture-v1` metadata uses adapter ID
`codex-app-server-stdio-v1`. It binds the binary, protocol, run context, copied
request/response data, raw byte references, observed thread/turn IDs, collaboration
edges and detached reviews. Foreign events, failed/unknown/duplicate responses,
timeouts and pending approvals cannot establish complete evidence. Raw captures
must be independently interpreted; metadata is not acceptance authority.

`complete_accounting` and `host_semantics_verified` remain false. A known-thread
snapshot is not a durable complete-attempt census; child waiting is not evidence
of a scheduler wakeup. The central benchmark verifier still admits only the
existing CLI receipt contract. Four host-event evaluators and the complete
root/child/retry/review/recovery/clarification census remain separate work.
The proposed consumer obligations and all four fixed invariant maps are in
[CENSUS_CONTRACT_PROPOSAL_V1.md](CENSUS_CONTRACT_PROPOSAL_V1.md).

## Request pricing derivations

The existing audit extractor supports desktop `token_usage_record` alongside
legacy `event_msg` / `token_count`. It validates all six counters and exact
thread/session/turn/response identities, preserves desktop ordinals and original
legacy event indexes, and reconciles turn/thread cumulative boundaries. Duplicate
representations use the first observed ledger form within the same turn and
segment. If that form was legacy with an unknown request ID, the later desktop
response ID remains in parsed raw evidence; it does not rewrite an existing v1
row. Ambiguous mixed index ordering rejects export explicitly. Tests import a
legacy prefix and then its appended mixed export into the real hook ledger,
preserving duplicate replay and counting each request once. Source CLI version
continues to come from its own session metadata.

The standalone calculator consumes parsed request counts and observed product,
model, mode, regional processing and request input size. It requires request input
to equal the usage vector's input total, including cached input. Cumulative
aggregates cannot establish a per-request long-context multiplier.

Rates, applicability, multipliers, exemptions and the actual retrieval timestamp
are frozen into the complete rate-card digest from the
[official Codex USD rate card](https://help.openai.com/en/articles/20001415).
This is a published Codex-equivalent model-token estimate. It does not establish
the account's billed amount or API pricing. Unknown configuration remains
unpriced. Cache and reasoning subsets are not counted twice; Codex cache writes
are uncharged. Separate feature fees remain excluded and unverified.

From `codex-workflow/`:

```powershell
py -3 -B -m benchmark.runner derive-pricing --usage C:\evidence\usage.json --contexts C:\evidence\contexts.json --hook-source C:\source\codex-workflow-hooks --hook-usage-digest sha256:EXACT_USAGE_SOURCE_DIGEST --output C:\evidence\pricing.json
```

The contexts file uses `schema_version: codex-request-pricing-contexts-v1` and a
`contexts` list. Each record contains `source_id`, `event_id` and a `context`
object with `observed_product`, `observed_scope`, `observed_model`,
`observed_mode`, `observed_regional`, `observed_request_input_tokens`.

The command reuses the hook-owned usage validation and summary API, preserving
its replay checks. It leaves the original observations byte-for-byte unchanged.
Its `codex-request-pricing-derivation-v1` artifact binds usage/context digests,
calculator/hook/rate-card pins and each original source/event/task identity to
the request estimate. Context authenticity, complete accounting and total task
cost remain unverified; it never returns an aggregate task price or promotion.

The central verifier needs a coordinated derivation/context/census contract
before these estimates can satisfy its monetary gate. Original unpriced usage
rows must not be overwritten or re-ingested with changed prices under the same
ledger identities. The derived artifact is retained separately.
