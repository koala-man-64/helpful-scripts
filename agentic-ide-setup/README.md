# Agentic IDE Setup

Portable Windows PowerShell setup for the current Codex, Claude Code, and VS Code chat workflow. The committed profile contains selected settings plus user-authored agents, skills, rules, and hooks. It deliberately omits account details, sessions, caches, databases, and project-local configuration.

## Captured baseline

| Tool | Current version |
| --- | --- |
| Codex CLI | 0.116.0 |
| Claude Code | 2.1.236 |
| VS Code | 1.125.1 |

VS Code installs the current ChatGPT and Claude Code extension versions from `profile/vscode/extensions.txt`, then adds GitHub Copilot and Copilot Chat at their current Marketplace versions.

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

All three scripts require PowerShell 7 (`pwsh`). They write with `-Encoding utf8NoBOM`, which Windows PowerShell 5.1 rejects with a parameter-binding error partway through the export, after the exporter has already cleared `profile/`. Install PowerShell 7 before running them; recover an interrupted export with `git restore --source=HEAD --worktree -- agentic-ide-setup/profile`.

Run from this directory after reviewing local configuration changes:

```powershell
.\scripts\Export-AgenticIdeSetup.ps1 -Force
.\scripts\Test-AgenticIdeSetup.ps1
```

The exporter has an allowlist and produces templates with portable path markers. It captures only the custom Codex skills named by the bundle, avoiding runtime-managed packages. It also removes machine-specific Codex project state and replaces restricted MCP fields with `__REVIEW_REQUIRED__`.

## Install on a new Windows machine

Install Codex, Claude Code, and VS Code first. Review the profile, then preview the actions:

```powershell
.\scripts\Install-AgenticIdeSetup.ps1
```

Apply selected components only after the preview is correct:

```powershell
.\scripts\Install-AgenticIdeSetup.ps1 -Components Codex,Claude,VSCode -Apply
```

Existing files are preserved by default. Use `-Overwrite` only when replacing an existing profile file is intentional; the installer creates a timestamped sibling backup before replacement. Use `-DestinationRoot C:\Temp\agentic-home` to validate a complete install without changing the active profile.

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
