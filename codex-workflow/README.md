# Canonical Codex workflow catalog

`codex-workflow` is the advisory, versioned v2 catalog for global Codex instructions,
skills, profiles, and distribution. Catalog tooling does not install configuration
or mutate runtime state. The separate, explicitly invoked recovery helper below
is an operator-owned incident tool.

## Authority boundary

`codex-workflow-hooks` owns enforced mutation and evidence policy. This repository
owns the advisory global configuration, skill catalog, and distribution metadata.
The precedence order is:

1. Platform safety
2. Central hooks and policy
3. Global instructions
4. Repository instructions
5. Skill guidance

The catalog describes choices; it cannot relax a higher-precedence rule. One record
authority exists for each fact: hooks for mutation and evidence, Azure Boards for
tracked delivery, and CSV/JSONL only when coordination or a regulated audit
explicitly requires it.

`exports/codex-skills` and `exports/codex-hooks` are immutable legacy/reference
snapshots. Catalog entries may cite them but tooling must never rewrite them.

## Catalog format

The `.yaml` catalog files intentionally use JSON-compatible YAML so the included
tools can stay standard-library-only. Source hashes cover every file beneath the
declared skill directory, in sorted relative-path order. Shared pins are computed
from immutable Git objects, not platform-specific working-tree bytes. Use:

```powershell
py -3 codex-workflow/tools/build_inventory.py exports/codex-skills/repo-local --output $env:TEMP\codex-workflow-inventory.json
py -3 codex-workflow/tools/validate_catalog.py codex-workflow --strict-origin --repo asset-allocation-contracts=C:\src\asset-allocation-contracts --repo asset-allocation-control-plane=C:\src\asset-allocation-control-plane --repo asset-allocation-jobs=C:\src\asset-allocation-jobs --repo asset-allocation-runtime-common=C:\src\asset-allocation-runtime-common --repo asset-allocation-ui=C:\src\asset-allocation-ui
py -3 codex-workflow/tools/render_consumer_lock.py codex-workflow --repository asset-allocation-ui --output $env:TEMP\consumer-lock.json
py -3 codex-workflow/tools/render_candidate_bundle.py codex-workflow --output $env:TEMP\codex-workflow-candidate --release-digest sha256:INSTALL_MANIFEST_DIGEST --tested-commit EXACT_TESTED_COMMIT --repo asset-allocation-contracts=C:\src\asset-allocation-contracts --repo asset-allocation-control-plane=C:\src\asset-allocation-control-plane --repo asset-allocation-jobs=C:\src\asset-allocation-jobs --repo asset-allocation-runtime-common=C:\src\asset-allocation-runtime-common --repo asset-allocation-ui=C:\src\asset-allocation-ui
```

The current schemas are `skill-manifest-v2.schema.json` and
`consumer-lock-v2.schema.json`; v1 schemas are historical only. Inventory and rendered-lock output paths are user-directed and outside the committed
catalog by default. The renderer always requires `--output`; it never installs a
consumer lock. Strict provenance requires exactly one explicit `--repo ID=PATH`
mapping for each of the five consumer repositories and rejects duplicate or missing
mappings. The historical `%USERPROFILE%\Projects` lookup is only a non-strict
compatibility fallback.
Observed unresolved forks are selection-blocked metadata and are never emitted as
runnable lock selections. A branch-local workflow-router source is a candidate only:
publish that source to `origin/main` first, then publish a catalog pin pointing at the
reachable commit. Consumers must not adopt a candidate lock. The legacy strict-branch export is deprecated because it
permits `--force-with-lease`; central hooks deny force-push and active locks preserve
that denial.

## Disabled candidate package

`candidates/` is a disabled, non-installed package for evaluation only. Its generated
output contains all fifteen existing v2 consumer-lock shapes, an envelope that binds
each lock hash to catalog, candidate-source, and hook-release digests, canonical
origin SHAs, and an exact helpful-scripts catalog-base tested commit. `activation` and `readiness` remain false;
they are informational state, not enablement authority. The hook-owned readiness
verifier requires canonical origins and independent content-free preflight and
readiness receipts for benchmark, run, and acceptance evidence before any readiness
decision. See [candidates/README.md](candidates/README.md) for the shared digest
recipe.

## Evidence boundary

[`tools/Repair-CodexHookBytecode.ps1`](tools/Repair-CodexHookBytecode.ps1) is a
preview-first recovery helper for an exactly diagnosed four-file bytecode cache.
It is not a catalog skill, runtime hook, installer, or activation mechanism. Its
[runbook](tools/HOOK_BYTECODE_RECOVERY.md) describes ownership, strict refusal
conditions, quarantine evidence, installed self-test, and bytecode prevention.

Source, CI, release, deployment, runtime health, and user-path proof are separate
evidence states. A catalog entry or generated lock validates catalog provenance only;
it does not establish any delivery or runtime evidence.
