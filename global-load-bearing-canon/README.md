# Reproduce the global load-bearing canon

## Goal

Install the same compact engineering-judgment guidance for Codex and Claude
Code across every repository used by one local user profile. The reusable text
is in [load-bearing-canon.md](load-bearing-canon.md).

This is behavioral guidance, not mechanical enforcement. Hooks, permissions,
managed policy, and provider safety controls remain authoritative.

## What was changed

On August 30, 2026, the exact contents of `load-bearing-canon.md` were added
once to each of these user-level files on Rudy's Windows profile:

- Codex: `C:\Users\rdpro\.codex\AGENTS.md`
- Claude Code: `C:\Users\rdpro\.claude\CLAUDE.md`

The Codex section was placed after the opening working-agreement paragraphs and
before `## Interaction Style`. The Claude file was empty, so the section became
its entire contents.

No repository `AGENTS.md` or `CLAUDE.md`, skill, agent, hook, hook matcher,
machine reason code, API contract, schema, or application file was changed.

## Where to install it elsewhere

Use the user-level instruction files for the account that runs each client:

| Client | Default user-level file | Scope |
| --- | --- | --- |
| Codex | `~/.codex/AGENTS.md` | Local Codex work across repositories |
| Claude Code | `~/.claude/CLAUDE.md` | Local Claude Code work across projects |

On Windows, `~` normally resolves to `%USERPROFILE%`. If `CODEX_HOME` is set,
put the Codex file in that directory instead of `%USERPROFILE%\.codex`.
If `CLAUDE_CONFIG_DIR` is set, put the Claude file in that directory instead of
`%USERPROFILE%\.claude`.

These files are local to one user and machine. Repeat the installation for
other users, machines, containers, remote hosts, or CI workers. They do not
configure Copilot, Claude on the web, generic API clients, or unrelated agents.
Native Windows and WSL use separate home and configuration directories, so
install the canon in both environments if both run these clients.

## Prerequisites

- Read/write access to the target user's Codex and Claude Code configuration
  directories.
- A UTF-8-capable editor that preserves all five terms exactly, including the
  four non-ASCII terms.
- An authenticated client when performing the optional fresh-session check.

## Installation

1. Inspect each target before editing. Back up any non-empty file so unrelated
   guidance can be restored if the merge is incorrect.
2. Open the applicable user-level file. Create its parent directory and file if
   they do not exist.
3. For Codex, first check whether `AGENTS.override.md` exists in the same global
   directory. A non-empty global override is selected instead of `AGENTS.md`,
   so either put the canon in the override or retire the override intentionally.
4. Copy the complete contents of
   [load-bearing-canon.md](load-bearing-canon.md) into each target exactly once.
   Preserve all existing guidance and keep the canon as a standalone section.
5. Do not duplicate it into repository guidance, skills, agents, or hooks merely
   to make it more visible.
6. Run the static and fresh-session checks below.

An agent can reproduce the edit from this folder with this bounded request:

> Read `global-load-bearing-canon/load-bearing-canon.md`. Add its exact contents
> once as a standalone section in the active user's global Codex `AGENTS.md` and
> global Claude Code `CLAUDE.md`. Preserve all existing content. Do not edit
> repository guidance, skills, agents, hooks, or machine reason codes. Report
> any global override or non-default configuration directory before claiming
> success.

## Does Codex or Claude need a restart?

No operating-system reboot or full application restart is required.

- **Codex:** start a new Codex task, CLI run, or TUI session. Codex builds its
  instruction chain once per run, so an already-running task should not be
  expected to reload the edited global file. Restarting the whole desktop app
  is normally unnecessary.
- **Claude Code:** start a new conversation/session, such as a new `claude`
  invocation. Claude Code loads `CLAUDE.md` files at session start. Restarting
  the machine is unnecessary. This file does not configure Claude on the web.

Starting fresh sessions is the deterministic activation step for both clients.

## Static verification on Windows

Run this from the repository folder containing this guide:

```powershell
$codexDirectory = if ($env:CODEX_HOME) {
    $env:CODEX_HOME
} else {
    Join-Path $env:USERPROFILE '.codex'
}
$codexDefaultGuide = Join-Path $codexDirectory 'AGENTS.md'
$codexOverrideGuide = Join-Path $codexDirectory 'AGENTS.override.md'
$codexGuide = if (
    (Test-Path -LiteralPath $codexOverrideGuide) -and
    -not [string]::IsNullOrWhiteSpace(
        (Get-Content -LiteralPath $codexOverrideGuide -Raw -Encoding UTF8)
    )
) {
    $codexOverrideGuide
} else {
    $codexDefaultGuide
}
$claudeDirectory = if ($env:CLAUDE_CONFIG_DIR) {
    $env:CLAUDE_CONFIG_DIR
} else {
    Join-Path $env:USERPROFILE '.claude'
}
$claudeGuide = Join-Path $claudeDirectory 'CLAUDE.md'
$snippetPath = '.\global-load-bearing-canon\load-bearing-canon.md'

$snippet = (Get-Content -LiteralPath $snippetPath -Raw -Encoding UTF8) -replace "`r`n", "`n"
foreach ($guide in @($codexGuide, $claudeGuide)) {
    $content = (Get-Content -LiteralPath $guide -Raw -Encoding UTF8) -replace "`r`n", "`n"
    $count = ([regex]::Matches($content, [regex]::Escape($snippet.TrimEnd("`n")))).Count
    if ($count -ne 1) {
        throw "$guide contains $count exact canon sections; expected 1."
    }
    "$guide`: OK"
}
```

Expected result: one `OK` line for each target. A missing file, altered wording,
or duplicate section fails the check.

## Fresh-session verification

1. Start a new Codex task from any repository and ask:

   > Quote the first sentence under `Load-bearing canon` from your loaded
   > instructions.

   Expected answer: `本手 and 火候, always.`

2. Start a new Claude Code session, run `/context`, and confirm the user-level
   `CLAUDE.md` appears under memory files. Ask the same question and expect the
   same sentence.

If the file is missing from a new session:

- Confirm Codex is using the expected `CODEX_HOME`.
- Check for a non-empty global Codex `AGENTS.override.md`.
- Confirm Claude Code is using the expected `CLAUDE_CONFIG_DIR` and that user
  settings were not excluded by the caller.
- Inspect repository and nested instruction files for contradictory guidance.

## Instruction precedence

Codex loads global guidance first, then repository and nested guidance down to
the working directory. More specific files appear later and can supersede a
conflicting global instruction. A global `AGENTS.override.md` is selected in
place of the global `AGENTS.md`.

Claude Code loads managed, user, project, local, and relevant nested guidance
into the session. More specific instructions appear later, but conflicting
natural-language rules are not deterministic enforcement; remove conflicts
rather than relying on ordering.

## Validation record for the original installation

- Each target contained one exact copy of the section.
- The exact opening canon line appeared only in the two intended active global
  instruction files within the audited Codex and Claude prompt/hook surfaces.
- Unicode readback succeeded for all five terms.
- A fresh, ephemeral Codex session recalled `本手 and 火候, always.` without
  reading files or using tools.
- Fresh Claude Code model validation was blocked by an expired OAuth token;
  static file validation passed. Re-authenticate before running the Claude
  fresh-session check.

## Rollback

Remove the complete `## Load-bearing canon` section from each user-level file,
preserving all surrounding content, then start new Codex and Claude Code
sessions. No hook, cache, repository, or application rollback is required.

## Official references

- [Codex custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Claude Code instructions and memory](https://code.claude.com/docs/en/memory)
