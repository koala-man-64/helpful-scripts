# Agentic IDE Setup

Portable Windows PowerShell setup for the current Codex, Claude Code, and VS Code chat workflow. The committed profile contains selected settings plus user-authored agents, skills, rules, and hooks. It deliberately omits account details, sessions, caches, databases, and project-local configuration.

## Captured baseline

| Tool | Current version |
| --- | --- |
| Codex CLI | 0.116.0 |
| Claude Code | 2.1.236 |
| VS Code | 1.125.1 |

VS Code installs the current ChatGPT and Claude Code extension versions from `profile/vscode/extensions.txt`, then adds GitHub Copilot and Copilot Chat at their current Marketplace versions.

## Refresh the profile

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

## Verify and recover

```powershell
codex --version
claude --version
code --version
code --list-extensions --show-versions
.\scripts\Test-AgenticIdeSetup.ps1
```

For account sign-in, plugins, VS Code sync, and MCP review, follow [SIGN-IN-CHECKLIST.md](../SIGN-IN-CHECKLIST.md). Repository-specific `.claude`, `.codex`, and `.vscode` files remain with their repositories and are not part of this global setup.
