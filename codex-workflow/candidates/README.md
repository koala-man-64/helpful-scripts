# Disabled candidate bundle

This directory is an advisory, non-installed candidate package. It must not be
copied to a live configuration location or used as activation authority.

`render_candidate_bundle.py` produces fifteen consumer-lock-v2 payloads (one for
each of five repositories and three lanes) plus a `candidate-bundle-v1` envelope.
The envelope records `activation: false` and `readiness: false`, canonical origin
SHAs, the exact helpful-scripts catalog-base tested commit, and content-free receipt arrays. The readiness
verifier owned by `codex-workflow-hooks` supplies and verifies receipts; neither a
render nor a boolean field enables anything.

The envelope's `validator_pins` binds the combined semantic implementation digest
and `validator:deterministic-semantic-v1` entrypoint digest. Its coordinated central
consumer update is still required before those pins establish promotion authority.

The outputs bind the installed manifest named by the central policy observation.
That observation records read-only bytes from installed v4 release
`0.6.3+sha.76e85adf338bddc4ac51f1bf2d4ef67e6f245120`, manifest
`sha256:927f3553597aee2bc5085e56271fa729ba16fb0229f7f3558bd9c99b63044c3e`.
The installed manifest and policy bytes were independently read and hashed.
Its provenance is a clean-tag local installation with `source_kind=git` and
`build_id=local-install-21211-76e85ad`; successful release 21211 is separate evidence
and does not make this installation a downloaded pipeline artifact.
After Rudy reported completing the refresh, the hooks owner independently verified
supported `hooks/list` from desktop version 0.153.4: all seven owned hooks enabled
and trusted for this exact manifest, zero errors, and unchanged configuration hash.
This producer records that stdout-only owner attestation. No result artifact or
digest was retained, so it is not an independently retained receipt. Per-route
admission evidence and actual spawn proof remain absent; readiness stays false.

The byte-for-byte prior installed observation is retained in
`central-policy-observation-c540d2c.historical.json`. The historical
source/parser observation and its c540 release-byte test remain unchanged.
Policy bytes are unchanged across these installed identities; their CRLF bytes
remain distinct from the normalized LF source snapshot. Historical user trust
confirmation does not transfer to the new manifest.
`canonical_origins` records catalog origin snapshot SHAs, not current consumer
validation receipts.

The v4 evaluator accepts the observed Standard parent (`Terra`/`medium`) to
child (`Luna`/`max`) and Critical parent (`Sol`/`high`) to child
(`Terra`/`high`) requests. This is source-evaluator compatibility only. Root
owner lanes are not evaluated as child pairs, and no observation asserts actual
spawn or route admission.

The v2 schema additively accepts child efforts `high` and `max`. The immutable
Contracts 14.1.0 `routing_contract` remains a truthful publisher reference to its
historical profiles. Current child execution-plan selections follow the separately
bound central policy; publisher metadata is not an override of that policy.
No schema field or published Contracts artifact is repinned. Consumer owners must
run the shared pinned renderer/schema validation for all three locks plus their
applicable repository checks. Local application/runtime consumption is unverified.

From the immutable helpful-scripts source checkout, validate all three copied
locks using the same implementation for each consumer (substitute its name and
the directory containing `lite.json`, `standard.json`, and `critical.json`):

```powershell
py -3 -B codex-workflow/tools/validate_consumer_candidate.py codex-workflow --bundle codex-workflow/candidates/outputs --repository asset-allocation-ui --locks C:\consumer-evidence\asset-allocation-ui
```

The command is read-only. It verifies the whole pinned bundle, each selected
lock's exact bytes, schema, and route plan. Its result explicitly leaves the
owner's repository checks and runtime consumption unverified; it is not a hook
readiness receipt or installation instruction.

To validate the existing consumer naming convention at its real repository
location, add `--filename-pattern '{lane}.consumer-lock.v2.json'`. The reader
records those actual paths and never renames or stages the consumer's files.

Digest recipe: `catalog_digest` uses the catalog tool's canonical directory hash.
`bundle_digest` is the candidate-source digest and uses the same recipe over every
candidate source, skill, reference, and manifest file (excluding generated `outputs/`
and `__pycache__/`) plus the implementation inputs enumerated by
`CURATED_REPOSITORY_PATHS` in `render_candidate_bundle.py` and every concrete
`benchmark/task_inputs/` fixture file (excluding `__pycache__/`). The
recipe sorts UTF-8 relative POSIX paths, writes a four-byte big-endian path length
followed by the path bytes, then an eight-byte big-endian content length followed by
raw bytes, and hashes that stream with SHA-256. Each lane-lock hash is SHA-256 of its
exact UTF-8 JSON output bytes. `release_digest` is an explicit SHA-256 digest of the
pinned hook-release manifest provided by the caller. Every lane-lock receipt
repeats all three binding digests.
