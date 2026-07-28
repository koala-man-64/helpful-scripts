# Codex Workflow Hooks Reference

This directory stores reference-only snapshots of the centralized
`codex-workflow-hooks` runtime. These files are not active hook wiring for
`helpful-scripts` and must not become a second authoritative implementation.

## v0.1.2

- Source repository: `codex-workflow-hooks`
- Source tag: `v0.1.2`
- Source commit: `615598b6c47a40d9c2845df86af308baeb87d86d`
- Manifest digest:
  `eef607d3e7866da4960c50f6bd593c46d15063d2dd908b6c3004ed01b86588fe`
- Snapshot path: [`v0.1.2`](v0.1.2)

The snapshot contains only the manifest-covered launcher, runtime package,
global policy, repository overlays, and overlay schema. It intentionally
excludes user hook configuration, trust state, installation state, evidence
databases, backups, repository registrations, caches, build outputs, and
developer tooling.

Use the standalone source repository for changes and releases. When a newer
release needs to be retained here, add a new versioned directory rather than
editing an existing snapshot.
