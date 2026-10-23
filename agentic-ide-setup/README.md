# Agentic IDE Setup

Portable Windows PowerShell setup for the current Antigravity (Gemini), Codex, Claude Code, and VS Code chat workflow. The committed profile contains selected settings, user-authored agents, and required skills. The PowerShell installer copies files only and never activates the repository-owned Claude hooks. To recreate a whole Claude Code machine, including the hook release clone, settings, plugins and MCP servers, use [`scripts/claude_setup.py`](#move-claude-code-to-another-machine). It deliberately omits account details, sessions, caches, databases, and project-local configuration.

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
  - every force push, including `--force-with-lease`, `--force-if-includes`, `-f`, bundled `-uf` and `+ref`
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

To check the installed Codex guidance and source-owned skills, then preview the
Codex-only settings needed to remove duplicate `.agents` skill discovery:

```powershell
python -B .\scripts\check_codex_drift.py
```

The check compares only `profile/codex/AGENTS.md` and files in
`profile/codex/skills/`. It intentionally excludes host configuration, secrets,
backups, and skills owned elsewhere. A duplicate exists only when both
`.codex/skills/<name>/SKILL.md` and `.agents/skills/<name>/SKILL.md` exist.
After reviewing the printed config diff, `--apply-dedup` backs up
`.codex/config.toml` and disables only duplicate `.agents` paths through
`[[skills.config]]` entries. The `.agents` files remain available to other
clients. Install updated source guidance separately with the existing installer
and `-Components Codex -Overwrite -Apply`; it preserves the host config.

For a scoped skill repair, select only its canonical files and duplicate paths:

```powershell
.\scripts\Install-AgenticIdeSetup.ps1 -Components Codex `
  -CodexSkills agentcoord,project-workflow-enforcer-agent -Overwrite -Apply
python -B .\scripts\check_codex_drift.py `
  --skill agentcoord --skill project-workflow-enforcer-agent
```

Review that preview, then add `--apply-dedup` to disable those duplicate `.agents`
entries for Codex. Selected skills must exist in this committed profile. This
selection preserves AGENTS.md, unrelated skills, host settings and other clients'
skill files. Every replaced file and config retains its existing backup behavior.

Apply refuses missing or different canonical files and differing duplicate skills
that this profile does not own. The preview names the retained Codex path and each
differing file. Existing disabled entries stay disabled. Portable markers are
expanded only in source text, using the installer's `__USERPROFILE__`,
`__APPDATA__`, and `__LOCALAPPDATA__` markers.

Serialize other config writers, including Desktop trust/settings changes and
agentcoord updates, before applying. The tool checks fresh raw bytes, file identity,
permissions, and ancestors immediately before atomic replacement; this detects
observed edits but cannot provide an OS-level compare-and-swap against an
uncoordinated writer. Exact-byte sibling backups and replacement files retain the
original permissions (including the Windows DACL). To roll back, stop other writers,
review changes made since the backup, then restore the selected backup; blindly
restoring it after later settings changes would discard those changes. The installer
separately creates sibling backups for overwritten guidance files.

The Claude profile ships the `agent-browser` skill (`profile/claude/skills/agent-browser/`), which expects the CLI from this repository's `agent-browser/` folder on `PATH`. After the profile install:

```powershell
$env:PIP_INDEX_URL = "https://pypi.org/simple"; $env:PIP_EXTRA_INDEX_URL = ""; $env:PIP_NO_INPUT = "1"
python -m pip install --user "playwright>=1.61,<2"
python -m pip install --user -e ..\agent-browser
agent-browser doctor
```

## Move Claude Code to another machine

[`scripts/claude_setup.py`](scripts/claude_setup.py) captures this machine's Claude Code setup and recreates it on another one. It needs Python 3.10 or newer and nothing else: no packages, no PowerShell 7. The hooks themselves need Python 3.11 or newer.

| What | Travels in | Restore recreates it with |
|---|---|---|
| `CLAUDE.md` | `profile/claude/CLAUDE.md` | a copy |
| Agents and skills | `profile/claude/` (repository-owned) | a copy |
| Settings: permissions, hook wiring, status line, plugins, model, effort, env, preferences | `snapshot/claude/settings.template.json` | a merge into `settings.json` |
| User-scope MCP servers | `snapshot/claude/mcp-servers.template.json` | `claude mcp add-json --scope user` |
| Hook release clone and plugin repositories | `snapshot/claude/repositories.json` | `git clone`; the hook release is sparse and pinned to the commit this machine runs |
| Plugins | the settings' `extraKnownMarketplaces` and `enabledPlugins` | `claude plugin marketplace add`, `claude plugin install` |
| Docker MCP Toolkit servers | `snapshot/claude/docker-mcp-servers.txt` | `docker mcp server enable` |
| agent-browser CLI | this repository | `pip install --user -e agent-browser` |
| Auto-memory | a private zip, never this repository | a copy, with project folders renamed for the new home |

On this machine, from this directory:

```powershell
py -B .\scripts\claude_setup.py export
py -B .\scripts\claude_setup.py bundle
```

`export` refreshes `profile/claude/CLAUDE.md` and `snapshot/claude/`, then lists every entry of `~/.claude` it leaves behind and anything it does not recognize. It writes nothing in these cases:

- installed agents or skills differ from the profile (update the profile through a PR, or pass `--allow-drift`)
- any captured setting, MCP server field or line of `CLAUDE.md` looks like a credential or personal data:
  - a credential-named key
  - a URL with a user name
  - a token-shaped value
  - a password passed as an argument or assigned in a command (`PGPASSWORD=...`)
  - an email address

  A `${NAME}` reference, which the restoring machine resolves, is kept as written.
- a path stays machine-specific
- a repository the settings run code from lives anywhere other than `~\.claude\hooks-release` or directly under `~\Projects`

`--exclude-mcp NAME` leaves one MCP server out. Settings keys outside `claudeSnapshot.settingsKeys` in [setup-manifest.json](setup-manifest.json) are reported, not captured. The snapshot also carries the reviewed `deny` and `ask` rules from `profile/claude/settings.template.json`.

This repository is public, and the snapshot is executable configuration: its hooks, status line, MCP servers and plugins run on every machine that restores it. Review a snapshot diff like code before you commit or merge it. Clone URLs are published too, so keep private repositories out of the settings you export.

`bundle` writes `claude-private-bundle-<time>.zip` to your home folder and refuses any folder below a `.git`. It holds your memory notes in plain text, so keep it out of synced folders, move it privately, and delete it after the restore.

On the new machine:

1. Install Git, Python and Claude Code (`irm https://claude.ai/install.ps1 | iex`). Restore checks the other tools and prints a `winget` command for each one that is missing.
2. Clone this repository and preview the restore. The preview starts with everything the snapshot will run, clone or trust, in full. Then apply it; `--apply` shows the same plan and asks you to type `yes`:

   ```powershell
   git clone https://github.com/koala-man-64/helpful-scripts.git "$HOME\Projects\helpful-scripts"
   cd "$HOME\Projects\helpful-scripts\agentic-ide-setup"
   py -B .\scripts\claude_setup.py restore --private-bundle <zip>
   py -B .\scripts\claude_setup.py restore --private-bundle <zip> --apply
   ```

   Without a terminal to confirm in, add `--yes` after reviewing the preview.

3. Follow the manual steps it prints: sign in to Claude Code, authenticate `gh` and `az`, authorize Docker MCP servers that need an account, and run `agent-browser doctor`. Then start a new Claude Code session.

Restore is idempotent; rerun it after fixing anything it reports. Before it changes anything, it refuses a snapshot outside the shapes export writes. Such a snapshot has one of these:

- settings keys outside `claudeSnapshot.settingsKeys`
- a `bypassPermissions` default
- a clone outside the two allowed folders
- a directory marketplace outside the cloned repositories
- a value that could pass as a command-line option

It never replaces a profile or memory file that differs without `--overwrite`, which backs the file up first.

Each clone is made in a temporary folder and renamed only when every step succeeds. `settings.json` is left untouched until every hook script, status line script and marketplace folder it names exists; a hook whose script is missing would block every tool call. Settings are merged rather than replaced: template values win, lists gain missing entries, and a hook group never registers twice. `settings.json` is backed up before every write, and restore stops if another process changes the file while restore runs.

A user-scope MCP server that already exists with a different definition is left alone. Restore refuses links and junctions on every path it writes. Broken hook wiring or a settings merge that did not land fails the final verification.

`--home <folder>` stages a restore in another folder: `claude` commands run with `CLAUDE_CONFIG_DIR` inside it, and the machine-wide Docker and pip steps are skipped. A restored hook release stays at the pinned commit; deploy later hook changes with the release procedure below.

Left behind on purpose: credentials (sign in again), prompt history, session transcripts, caches, folder trust, and this machine's task state (`state`, `waits`, `task-notes`, `scheduled-tasks`). Paths are stored as `__USERPROFILE__`, `__USERPROFILE_SLASH__` and `__USERPROFILE_POSIX__` markers and `%ProgramFiles%`-style names, so a different user name works.

## Claude hooks: release clone

The PowerShell installer does not install Claude hooks. They run from a pinned release clone of this repository at `%USERPROFILE%\.claude\hooks-release`: a sparse clone, detached at a merged commit, that `settings.json` points at. Nothing is copied, so a hook edited in place shows up in `git status` instead of silently drifting from this repository. On a new machine, `claude_setup.py restore` performs the one-time setup below and wires `settings.json`.

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

