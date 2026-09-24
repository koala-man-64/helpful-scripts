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

The Claude template does carry restrictions: `claudeDefaults.permissions.deny` and `.ask`. These are restrictions, not grants.

- **Deny:**
  - discarding work: `git reset --hard`, `git checkout -- …` and `checkout .`, `git restore .`, and force `git clean` with directories
  - force pushes other than `--force-with-lease`, including `-f`, bundled `-uf` and `+ref`
  - pushes to `main`, `master`, `trunk`, `develop`, `staging` or `production`

  Each rule also has a `git -C <path>` twin, for both Bash and PowerShell.
- **Ask:** `git checkout <treeish> -- <paths>`.

Claude Code applies these rules even when a PreToolUse hook returns `allow`, and when a hook times out, so they back up the shell guard hook rather than replace it. Text rules can't see repository state, so the guard alone covers these cases:
- a bare `git push` on a protected branch
- `--all` and `--mirror` pushes
- `git branch -D`, which squash merges make routine for merged task branches

A dry run with the force flag first (`git clean -fdn`) is caught by the deny rules; use `git clean -n -d`.

Shell allow rules on the host (`Bash(...)` and `PowerShell(...)` entries in `permissions.allow`) matter only when the guard returns no decision, for example when its process fails to start or times out. For every command the guard assesses, its own allow, ask or deny decides.

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

## Claude hooks: release clone

The bundle does not install Claude hooks. They run from a pinned release clone of this repository at `%USERPROFILE%\.claude\hooks-release`: a sparse clone, detached at a merged commit, that `settings.json` points at. Nothing is copied, so a hook edited in place shows up in `git status` instead of silently drifting from this repository.

One-time setup:

```powershell
$clone = "$HOME\.claude\hooks-release"
git clone --filter=blob:none --no-checkout https://github.com/koala-man-64/helpful-scripts.git $clone
git -C $clone sparse-checkout set --cone agentic-ide-setup/profile/claude/hooks
git -C $clone switch --detach origin/main
```

Back up `settings.json` (it stays host-owned). Then point each Claude hook command at `...\.claude\hooks-release\agentic-ide-setup\profile\claude\hooks\<hook>.py`, and run each command once by hand with a sample payload before relying on it.

Deploy a merged hook change:

```powershell
git -C $clone status --porcelain    # must print nothing; otherwise upstream or discard the in-place edit first
git -C $clone rev-parse HEAD        # record it for rollback
git -C $clone fetch origin
git -C $clone switch --detach <merged-sha>
```

Then run the hook tests in the clone's hooks folder: `$env:PYTHONDONTWRITEBYTECODE = 1; py -m pytest -q -p no:cacheprovider`.

Deploy when few sessions are running, because every running session picks up changed hook files immediately. To roll back, run `git -C $clone switch --detach <previous-sha>`.

Check drift between this profile and the installed setup:

```powershell
py .\scripts\check_claude_drift.py --fetch
```

The check fails in any of these cases:

- the release clone has local edits
- the release clone isn't on `origin/main`
- `settings.json` still runs a copied hook
- installed agents, skills or `CLAUDE.md` differ from the profile (line endings are ignored)

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

