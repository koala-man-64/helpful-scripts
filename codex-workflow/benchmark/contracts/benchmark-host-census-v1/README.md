# Benchmark host census v1

This is a contracts-authoring change. This directory owns the shared, data-only
`benchmark-host-census-v1` interface and synthetic interoperability fixtures.
Bundle version: **1.0.0**. Schema dialect: **JSON Schema Draft 2020-12**.

## Authority and publication

The hand-authored `benchmark-host-census-v1.schema.json` is the structural source
of truth. It is outside the generated business `schemas/` tree. Neither the
Python `asset-allocation-contracts` nor the npm `@asset-allocation/contracts`
package contains this bundle; their versions do not change. Consumers need no
Pydantic runtime dependency. The producer constructs records. The central hooks
owner independently verifies artifacts, topology, usage, context and host
semantics, and owns any separately versioned admission decision.

The `WorkflowEvidenceContracts` CI job validates and publishes the exact directory
as artifact `benchmark-host-census-v1`. `publication.json` binds the artifact to
the actual CI checkout commit and manifest digest. Consumers must retain the
successful build ID, exact source commit, manifest digest and payload digest;
a branch name, local directory or successful schema test is not a publication
pin. Draft PR artifacts are review candidates. Adoption requires the approved
source and successful CI artifact; no release-feed publication is claimed.
CI artifacts are subject to retention, so consumers retain their adopted bytes.
Handlers must use those pinned local bytes, without network fetching.

`manifest.json` inventories every payload file except itself with a portable
relative path, byte length and SHA-256 digest. `payload_digest` is SHA-256 over
the UTF-8 JSON encoding of the sorted `files` array, with sorted object keys,
two-space indentation and one final LF (the `encode` function in the packaging
script). `publication.json` is generated provenance outside that payload
inventory; its `manifest_digest` seals the exact manifest bytes. Hashes prove
byte integrity only. Trust in source/build identity must come from independently
verified provider evidence. Never take expected admission pins from the census.

## Structural rules

All record fields are required, including nullable fields. Record objects reject
unknown properties. Artifact maps accept nonempty artifact IDs; request provenance
accepts only the seven declared context fields. Those explicitly typed maps are
the exceptions to closed record objects. The four semantic scenario IDs and
their invariant names are exact; missing evidence is represented by empty arrays
and explicit partial reasons. The envelope may contain a subset of scenarios;
the pinned study manifest determines which scenarios are required for a run.

Integers exclude booleans. Digests are `sha256:` plus 64 lowercase hex digits.
Timestamps use UTC RFC3339 with `Z` or `+00:00`; consumers must assert `date-time` format,
including real calendar dates. Artifact paths use forward-slash-separated
portable components beginning with a letter, digit, underscore or hyphen;
subsequent characters may also include periods. Absolute paths, traversal,
backslashes, drive/stream colons and URI syntax are rejected. This is a lexical
constraint; the verifier must also reject symlinks/reparse points and resolved
paths outside its retained artifact root.

No structural success authorizes acceptance. Required nullable values need
explicit partial reasons when unavailable. The central verifier derives missing
evidence from raw records even if caller `partial_reasons` is empty. It checks
full-file size/digest/seal, exact frame ranges/digests, UTF-8/JSON framing,
supported record semantics and artifact kinds, unique IDs, graph ownership,
segment predecessor chains, segment-scoped RPC identities, exact request sets,
request/response/source/event joins, terminal history and cutoff consistency.
Normalized usage identity is based on raw `session.session_id`, not
`attempt.thread_id`. An explicit retained thread-to-session binding is required
across those namespaces. Missing binding stays partial; contradictory binding
rejects. Neither equal-looking IDs nor timestamp proximity supplies that binding.
Malformed JSON, duplicate object keys and nonfinite values must be rejected
before schema validation.

Unknown retries, gaps, pending approvals, failed shutdown, unstable cutoffs,
missing terminals and unexhausted pagination remain partial. Contradictions
reject. A later successful turn cannot erase failed or omitted work. Existing
hook-owned usage validation remains the sole replay and token-accounting
authority; never duplicate cumulative counts as new usage.

Pricing requires request-effective product, scope, model, mode, region and exact
input size with authentic per-field provenance, immutable usage rows and an
independently pinned rate card. Missing evidence stays unpriced; contradictory
provenance rejects. Independently recompute any separate price derivation and
label it a published Codex-equivalent estimate, never actual billed cost. This
envelope does not introduce a new usage or price payload.

All three reconciliation flags are permanently false in this producer version.
Host scope closure and the external wait's exactly-once scheduler history remain
provider-blocked. A host-event label, prepared peer file, review pass flag,
compaction acknowledgement, child wait or manual resume supplies no missing
authority. `fixture-op-17` and its pending revision 1, completed revision 2 and
single `summarize consumer validation` continuation must remain the exact external
wait fixture; no substitute operation or heartbeat can prove that history.

## Shared fixtures

`fixtures/index.json` names each `census_file`, its retained `artifact_root`,
`json_valid`, `schema_valid`, `expected_disposition` and the reason for that
expectation. Paths are relative to `fixtures/`. `json_valid` means strict JSON
decoding, including duplicate-key rejection. `schema_valid` includes asserted
timestamp formats. A decode failure also has `schema_valid: false`.

`admission_config_file` supplies independent synthetic expected pins for the
pin-mismatch cases. The duplicate-key decoding case does not need admission
inputs. `pricing_checks_file` points to supplemental synthetic adapter inputs and
mutations for the existing usage/pricing authority: unchanged usage, explicit
request context, a synthetic rate card, a fixed decimal result and seven pricing
expectations. These test inputs introduce no new production usage or price schema.
Consumers map them through their existing adapters and retain the independent
input digests. No synthetic rate is a current product price.

`expected_disposition` is the required outcome of the case's focal semantic or
pricing check: `reject`, `partial` or `unpriced`. It is not an overall admission
result or a required failure-priority ordering. In particular, an unpriced case
is also incomplete. Every fixture remains synthetic and non-admissible; none
represents a completed study run. `semantic_expectations_executed: false` records
that the authoritative consumer must still run its own checks. Structural
validation is tested here; semantic expectations are shared acceptance requirements.
Synthetic frames deliberately contain no supported host receipt authority.

Fixture definitions are maintained in `scripts/build_benchmark_census_fixtures.py`;
the committed JSON and raw files are the shared consumer inputs. Reproduce them
and seal the bundle after any intentional edit:

```powershell
python scripts/build_benchmark_census_fixtures.py
python scripts/package_benchmark_host_census.py --write-manifest
python -m pytest tests/python/test_benchmark_host_census_contract.py -q
python scripts/build_benchmark_census_fixtures.py --check
python scripts/package_benchmark_host_census.py
```

Changes after first approved publication require a new bundle version and a new
immutable source/artifact pin. Breaking envelope semantics require a new schema
version. Consumers explicitly adopt the reviewed pin; a schema or fixture update
does not activate an adapter, dispatch a benchmark, or relax the 72-run gate.
