# Portable setup remediation and rollout

Tracking: [AB3564](https://dev.azure.com/rdprokes/AdaptiveAssetAllocation/_workitems/edit/3564).

## Authority and scope

`setup-manifest.json` lists required skill sources, destinations, fresh-install
defaults, extension IDs, and external dependencies. Its copy in `profile/` is the
installation inventory. Export refreshes installed working agreements but uses
reviewed manifest defaults instead of copying the current session's preferences.
Current AGENTS.md and managed policy override imported historical specialist guidance.

The workflow enforcer is curated in the repository's Codex profile and exported to
both clients' skill directories. This resolves the older unconditional orchestrator
workflow. The Claude destination also satisfies repository hooks that discover
definitions in `.claude/skills`; the Codex router is installed in `.codex/skills`.
Discovery must be checked in a fresh client session after adoption. Disk presence
alone does not prove that a running session loaded the skill.

The browser skill is repository-owned in `profile/claude/skills/agent-browser`.
Its examples are checked against the CLI parser by `agent-browser/tests/test_skill.py`.
Other user skill sources are explicit home-relative paths in the manifest. Export
fails when any required source is missing; it does not silently substitute a fork.

## Managed dependencies

The bundle never installs or activates hooks. Existing host config/settings/MCP
files are preserved even with `-Overwrite`, so their hook registration, permissions,
and trust records cannot be erased by a profile refresh. Historical Claude hook
source and tests remain in the repository; the profile installer skips that tree.

Install Codex workflow hooks through the owning `codex-workflow-hooks` repository's
immutable-release installation procedure and run its `hookctl.py doctor --json`.
Use the owner's runbook for version selection, generated definitions, trust, and
rollback. Do not copy release paths or trust hashes between machines. Claude hooks
and agentcoord likewise require their owning installer and health checks. Their
absence is a reported dependency, not proof of a complete runtime setup.

Python 3.11+ supplies the standard-library TOML parser; no Python package is added.
Install the browser CLI, Playwright, and Edge/Chrome as described in the main README.
Install/authenticate plugins separately from `profile/codex/plugins.txt`; the list
includes configured entries whether enabled or disabled and grants no permissions.
Configure MCP servers explicitly; the shipped MCP configuration is empty.

## Validation and rollout

1. Run `python -B -m pytest agentic-ide-setup/tests agent-browser/tests/test_skill.py`.
2. Run `pwsh -NoProfile -File agentic-ide-setup/scripts/Test-AgenticIdeSetup.ps1`.
3. Preview and apply to an empty temporary destination using `-DestinationRoot`.
   This operation is files-only. `-InstallExtensions` is rejected for alternate homes.
4. Review the generated profile diff, then deliver through the repository PR gates.
5. Preview the selected components against the active home. Default installation
   adds missing files and preserves existing ones. Review guidance replacements
   before using `-Overwrite`; merge host settings separately.
6. Start fresh client sessions and verify the router, enforcer, and browser skill
   are advertised. Run each installed hook owner's doctor and one browser smoke
   check. Record these as runtime evidence, separately from repository tests.

The installer validates the entire profile and destination ancestors before writes.
It refuses reparse points on affected paths, unresolved config markers, machine-bound
executable settings, and missing required skills. Export uses same-volume directory
renames only after staged validation; a publish failure attempts to restore the
previous snapshot. A crash between renames can leave the backup needing recovery.

## Recovery and ownership

Export retains the prior snapshot in the reported `profile.backup-*` directory;
failed exports retain their diagnostic `profile.stage-*` directory. These are ignored
by Git. Inspect exact resolved paths before any manual move or deletion. Retain useful
failure evidence; remove task-created temporary fixtures when no longer needed.

Installation is per-file, not a transaction. If an I/O failure interrupts it, inspect
the reported error and unique `*.agentic-ide-setup-backup-*` files, restore affected
files, and rerun validation. Host configuration and managed hooks remain untouched.
The installer owns file copying only; hook installers own hook lifecycle, clients
own plugin authentication, and the user owns protected approvals.
