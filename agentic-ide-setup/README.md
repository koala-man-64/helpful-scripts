# Agentic IDE Setup

Portable Windows PowerShell setup for the current Antigravity (Gemini), Codex, Claude Code, and VS Code chat workflow. The committed profile contains selected settings, user-authored agents, and required skills. Repository-owned Claude hook source remains available for maintenance but is not installed or activated by this bundle. It deliberately omits account details, sessions, caches, databases, and project-local configuration.

## Managed baseline

The September 16, 2026 refresh uses [setup-manifest.json](setup-manifest.json)
as the required-skill and default-settings inventory. Python 3.11+ and PowerShell
7+ are required. Host applications, authentication, plugins, and managed hooks are
installed separately; their versions are not pinned by this bundle.

Fresh Codex profiles use Terra/medium and retain the existing never-approve/full-access policy.
Fresh Claude profiles use Sonnet/high. Existing host configuration is preserved,
even with `-Overwrite`; merge intended preference changes separately. The current
machine's Astra/medium preference is not a portable routing requirement.

See [remediation and rollout](docs/setup-remediation.md) for boundaries, validation,
managed dependencies, and rollback. Extension IDs track Marketplace versions and
are installed only with the explicit `-InstallExtensions` option on the active home.

## Planned enterprise Cline and Kilo Code documentation

The [enterprise Cline and Kilo Code documentation implementation plan](docs/cline-kilo-vscode-enterprise-implementation-plan.md)
defines the administrator guidance, Windows and WSL walkthroughs, terminal/browser/MCP
canaries, validation matrix, rollout, and rollback work. The plan is documentation-only;
it does not add either extension to the portable installer.

## Agent wait-scheduling and peer coordination

The [Codex wait-scheduling repair brief](docs/codex-wait-scheduling-repair.md) records a
`codex-workflow-hooks` audit: the asynchronous wait/follow-up feature has never executed, because
its trigger requires a `pushed` delivery artifact that the recorder never writes. It lists the
defects in fix order with reproduction queries and a validation plan.

The [Claude wait-scheduling and peer coordination plan](docs/claude-wait-scheduling-and-peer-coordination.md)
maps the same capability onto Claude Code, which already has the scheduling and peer-visibility
primitives. It covers what exists, the one missing hook and registry, and the design rule that
keeps the Claude implementation from repeating the Codex failure.

Both documents are analysis and planning only; neither changes installed hooks or profiles.

## Refresh the profile

Scripts reject PowerShell versions below 7 before writing. Export first creates a
sibling staging directory, validates it, and only then replaces `profile/`. The
previous snapshot is retained in an ignored `profile.backup-*` directory. Failed
exports preserve the previous snapshot and leave a diagnostic staging directory.

Run from this directory after reviewing local configuration changes:

```powershell
.\scripts\Export-AgenticIdeSetup.ps1 -Force
.\scripts\Test-AgenticIdeSetup.ps1
```

The exporter follows the manifest and produces templates with portable path markers. It excludes machine-specific project state, hook trust, permission grants, notification executables, and installed MCP commands. The MCP template starts empty; plugins are an inventory for separate reviewed installation. Required skill absence fails export.

The Claude template does carry one kind of permission: `claudeDefaults.permissions.deny`, a list of restrictions rather than grants. It denies discarding work (`git reset --hard`, `git checkout -- …`, `git restore .`, force `git clean`), force pushes other than `--force-with-lease`, and pushes to `main`, `master`, `trunk`, `develop`, `staging` or `production`, for both Bash and PowerShell. Claude Code applies deny rules even when a PreToolUse hook returns `allow` and when a hook times out, so the list is a backstop for the shell guard hook, not a replacement for it. `git branch -D` is deliberately left to the guard: squash merges make `git branch -d` unusable for merged task branches.

## Install on a new Windows machine

Install Codex, Claude Code, and VS Code first. Review the profile, then preview the actions:

```powershell
.\scripts\Install-AgenticIdeSetup.ps1
```

Apply selected components only after the preview is correct:

```powershell
.\scripts\Install-AgenticIdeSetup.ps1 -Components Codex,Claude,VSCode -Apply
```

Existing files are preserved by default. Use `-Overwrite` only for intentional guidance or skill replacement; each replacement receives a unique sibling backup. Host config, settings, and MCP files are always preserved when present. Use `-DestinationRoot C:\Temp\agentic-home` for a files-only staged install. Embedded path markers resolve against that destination, and extension installation is refused there.

The Claude profile ships the `agent-browser` skill (`profile/claude/skills/agent-browser/`), which expects the CLI from this repository's `agent-browser/` folder on `PATH`. After the profile install:

```powershell
$env:PIP_INDEX_URL = "https://pypi.org/simple"; $env:PIP_EXTRA_INDEX_URL = ""; $env:PIP_NO_INPUT = "1"
python -m pip install --user "playwright>=1.61,<2"
python -m pip install --user -e ..\agent-browser
agent-browser doctor
```

## Verify and recover

```powershell
codex --version
claude --version
code --version
code --list-extensions --show-versions
.\scripts\Test-AgenticIdeSetup.ps1
```

For account sign-in, plugins, VS Code sync, and MCP review, follow [SIGN-IN-CHECKLIST.md](../SIGN-IN-CHECKLIST.md). Repository-specific `.claude`, `.codex`, and `.vscode` files remain with their repositories and are not part of this global setup.

For the local Codex coordination pilot, follow
[AGENTCOORD-CODEX-PILOT.md](AGENTCOORD-CODEX-PILOT.md). It covers the local bridge,
OS-vault credential, isolated identity and SQLite state, MCP registration, lifecycle hooks,
behavioral guidance, validation, and rollback. It does not authorize or claim an Azure
production deployment.

For the Antigravity 2.0 setup, working agreements, 79-skill suite, safety hooks, and multi-model team orchestration, follow [ANTIGRAVITY-SETUP.md](ANTIGRAVITY-SETUP.md).

