# Canonical Codex workflow catalog

`codex-workflow` is the advisory, versioned v2 catalog for global Codex instructions,
skills, profiles, and distribution. It does not install configuration or mutate
runtime state.

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
declared skill directory, in sorted relative-path order. Use:

```powershell
py -3 codex-workflow/tools/build_inventory.py exports/codex-skills/repo-local --output $env:TEMP\codex-workflow-inventory.json
py -3 codex-workflow/tools/validate_catalog.py codex-workflow
py -3 codex-workflow/tools/render_consumer_lock.py codex-workflow --repository asset-allocation-ui --output .\consumer-lock.json
```

The current schemas are `skill-manifest-v2.schema.json` and
`consumer-lock-v2.schema.json`; v1 schemas are historical only. Inventory and rendered-lock output paths are user-directed and outside the committed
catalog by default. The renderer always requires `--output`; it never installs a
consumer lock. Observed unresolved forks are blocking metadata and are never emitted
as runnable lock selections. The legacy strict-branch export is deprecated because it
permits `--force-with-lease`; central hooks deny force-push and active locks preserve
that denial.

## Evidence boundary

Source, CI, release, deployment, runtime health, and user-path proof are separate
evidence states. A catalog entry or generated lock validates catalog provenance only;
it does not establish any delivery or runtime evidence.
