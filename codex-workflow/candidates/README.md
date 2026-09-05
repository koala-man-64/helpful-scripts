# Disabled candidate bundle

This directory is an advisory, non-installed candidate package. It must not be
copied to a live configuration location or used as activation authority.

`render_candidate_bundle.py` produces fifteen consumer-lock-v2 payloads (one for
each of five repositories and three lanes) plus a `candidate-bundle-v1` envelope.
The envelope records `activation: false` and `readiness: false`, canonical origin
SHAs, the exact helpful-scripts catalog-base tested commit, and content-free receipt arrays. The readiness
verifier owned by `codex-workflow-hooks` supplies and verifies receipts; neither a
render nor a boolean field enables anything.

The checked-in generated outputs are a historical v3 snapshot at release
`0.6.3+sha.3a7c21839d428f2240a21c238b85947bb62b1b17`. The separate central
policy observations record read-only bytes from installed v4 release
`0.6.3+sha.836bd53dacd62776b242e2f78e6f0140b7f9fd6e`; they distinguish an
installed policy-byte match from the historical source/parser check. Rudy's
completed reload, review, and trust are owner-confirmed user validation, recorded
separately from the direct byte observations. Neither establishes candidate routing
admission: per-route admission evidence and actual spawn proof remain absent.
Regenerate the outputs against the new observation before validating their binding.
`canonical_origins` records catalog origin snapshot SHAs, not current consumer
validation receipts.

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
