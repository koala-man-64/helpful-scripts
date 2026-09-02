# Reproduce the global load-bearing canon

## Goal

Install the same compact engineering-judgment guidance for Codex and Claude
Code across every repository used by one local user profile. This guide also
explains how VS Code and GitHub Copilot Chat discover equivalent user-level
instructions without creating an unnecessary duplicate. The reusable text is
in [load-bearing-canon.md](load-bearing-canon.md).

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
No `.copilot` instruction file, `.github/copilot-instructions.md`, or
`.github/instructions` file was created or changed.

## Where to install it elsewhere

Use the user-level instruction files for the account that runs each client:

| Client | Default user-level file | Scope |
| --- | --- | --- |
| OpenAI Codex, including its VS Code IDE extension | `~/.codex/AGENTS.md` | Local Codex work across repositories |
| Claude Code | `~/.claude/CLAUDE.md` | Local Claude Code work across projects |
| VS Code/GitHub Copilot Chat | `~/.claude/CLAUDE.md` when enabled, or `~/.copilot/instructions/load-bearing-canon.instructions.md` | Local VS Code Chat work across workspaces |

On Windows, `~` normally resolves to `%USERPROFILE%`. If `CODEX_HOME` is set,
put the Codex file in that directory instead of `%USERPROFILE%\.codex`.
If `CLAUDE_CONFIG_DIR` is set, put the Claude file in that directory instead of
`%USERPROFILE%\.claude`.

These files are local to one user and machine. Repeat the installation for
other users, machines, containers, remote hosts, or CI workers. The Codex
global file configures the OpenAI Codex IDE extension, but it does not configure
VS Code/GitHub Copilot Chat. VS Code can use its native user-level instructions
folder or reuse the global Claude file when `chat.useClaudeMdFile` is enabled.
These files do not configure Claude on the web, generic API clients, or
unrelated agents.
Native Windows and WSL use separate home and configuration directories, so
install the canon in both environments if both run these clients.

The OpenAI Codex IDE extension can run inside VS Code, but it is not VS Code/
GitHub Copilot Chat. Each surface has separate instruction discovery.

## VS Code and GitHub Copilot Chat

The OpenAI Codex IDE extension has no separate VS Code instruction path. It
uses the same `~/.codex/AGENTS.md` or `$CODEX_HOME/AGENTS.md` file as other
Codex clients.

This section is a **human-operator procedure**. The Claude Code handoff prompt
deliberately never writes a `.copilot` file: the only authoritative proof that
VS Code loaded an instruction file is the Chat view's Diagnostics panel, a GUI
action no command-line agent can perform. An agent gated on proof it can never
obtain always takes the same branch, so such a rule is dead text wearing the
costume of a safeguard. The agent inspects and reports; a person decides and,
where needed, writes.

For VS Code/GitHub Copilot Chat, choose exactly one user-level source:

1. Confirm that `chat.useClaudeMdFile` is enabled. VS Code enables this setting
   by default and can load `~/.claude/CLAUDE.md` for all workspaces.
2. Right-click the VS Code Chat view, select **Diagnostics**, and check whether
   `~/.claude/CLAUDE.md` is loaded without errors. The **References** section of
   a response also shows which instruction files were used.
3. If the global Claude file is loaded and already contains the canon, reuse it.
   Do not create a Copilot copy.
4. Otherwise, create
   `~/.copilot/instructions/load-bearing-canon.instructions.md` with this YAML
   front matter, followed by one unchanged copy of
   [load-bearing-canon.md](load-bearing-canon.md):

   ```markdown
   ---
   applyTo: "**"
   ---
   ```

Keep the front matter out of `load-bearing-canon.md`. `applyTo: "**"` makes the
native instruction file apply automatically to all files. On Rudy's Windows
profile, its absolute path is
`C:\Users\rdpro\.copilot\instructions\load-bearing-canon.instructions.md`.
Use the equivalent home directory for another user. Confirm that
`chat.instructionsFilesLocations` has not disabled the user-level folder.

Do not load the canon through both `~/.claude/CLAUDE.md` and the native
`.copilot` file in the same VS Code configuration. VS Code combines applicable
instruction sources, so duplication is unnecessary and makes conflicts harder
to diagnose.

The `.copilot` path above is user-level and available across workspaces.
Repository alternatives have narrower scope:

- `<repository>/.github/copilot-instructions.md` applies repository-wide.
- `<repository>/.github/instructions/*.instructions.md` is repository-scoped
  and pattern-based.
- `<repository>/AGENTS.md` can be loaded by VS Code Chat when
  `chat.useAgentsMdFile` is enabled, but it is workspace guidance, not the
  global Codex file under `~/.codex`.

Do not place a user-global canon in those repository paths merely to make it
available to VS Code.

## Prerequisites

- Read/write access to the target user's Codex and Claude Code configuration
  directories and, when using native VS Code instructions, the user's
  `.copilot/instructions` directory.
- A UTF-8-capable editor that preserves all five terms exactly, including the
  four non-ASCII terms.
- An authenticated client when performing the optional fresh-session check.

## Installation

1. Inspect each target before editing. Back up any non-empty file so unrelated
   guidance can be restored if the merge is incorrect. Write backups to
   `~/.canon-backups/<UTC timestamp>/` — never into `~/.codex`, `~/.claude`, or
   `~/.copilot/instructions`, and never with a `.instructions.md` suffix. VS
   Code discovers that folder by glob, so a backup left there becomes a second
   active instruction source: the duplicate-source failure this guide otherwise
   works hard to prevent.
2. Open the applicable user-level file. Create its parent directory and file if
   they do not exist.
3. For Codex, first check whether `AGENTS.override.md` exists in the same global
   directory. A non-empty global override is selected instead of `AGENTS.md`,
   so either put the canon in the override or retire the override intentionally.
4. Copy the complete contents of
   [load-bearing-canon.md](load-bearing-canon.md) into each target exactly once.
   Preserve all existing guidance and keep the canon as a standalone section.
   On first insertion, place it so it becomes the file's first `##` heading:
   after the opening `#` title and its introductory paragraphs, and before the
   first existing `##`. Leave an already-installed copy where it is — moving a
   correct body is a worse risk than imperfect placement.
5. Write with an editor or API that controls the byte order mark. Windows
   PowerShell 5.1's `Set-Content`, `Add-Content`, `Out-File`, and `>` always add
   a BOM under `-Encoding utf8` and cannot produce BOM-less UTF-8, so they
   silently alter a BOM-less target while still passing a substring check. Use
   the .NET API instead, passing `$true` only to preserve a BOM the file already
   had:

   ```powershell
   $encoding = New-Object System.Text.UTF8Encoding($false)
   [System.IO.File]::WriteAllText($path, $text, $encoding)
   ```

6. Do not duplicate it into repository guidance, skills, agents, or hooks merely
   to make it more visible.
7. Run the static and fresh-session checks below.

For a complete Claude Code handoff, give Claude access to this folder and paste
the prompt from
[claude-code-install-prompt.md](claude-code-install-prompt.md). It verifies the
sibling canonical body against a pinned hash instead of duplicating it, and
covers active config directories, Codex overrides, backups, encoding,
idempotence, static validation, live proof, and restart behavior. It installs
Codex and Claude Code only; VS Code is inspected and reported, and its
instruction file stays a human step.

### The pinned hash

The canonical body is identified by the SHA-256 of its normalized text — read as
UTF-8, leading BOM stripped, CRLF replaced with LF, trailing newlines removed:

```text
f41169c7ea9715014a023256b6e1d2d358a5021e4a16d9e7b100fd95d56bce8a  (427 bytes)
```

Content, not location, is the integrity control. Worktrees, nested checkouts,
and clones legitimately duplicate this folder, so an installer must tolerate
several copies and reject a body that fails the hash — not the reverse.

The pin appears in three places: the block above, the verification script below,
and the handoff prompt. **If the canonical body is ever edited, update all three
in the same change.** A stale pin blocks every install until it is corrected.

## Does Codex, Claude, or VS Code need a restart?

No operating-system reboot or full application restart is required.

- **Codex:** start a new Codex task, CLI run, or TUI session. Codex builds its
  instruction chain once per run, so an already-running task should not be
  expected to reload the edited global file. Restarting the whole desktop app
  is normally unnecessary.
- **Claude Code:** start a new conversation/session, such as a new `claude`
  invocation. Claude Code loads `CLAUDE.md` files at session start. Restarting
  the machine is unnecessary. This file does not configure Claude on the web.
- **VS Code/GitHub Copilot Chat:** no application restart or window reload is
  required. Saved instruction files are available to subsequent requests. A
  fresh chat is useful for a clean verification, but is not a loading
  requirement.

Starting a fresh session is the deterministic activation step for Codex and
Claude Code. It is an optional clean verification step for VS Code Chat.

## Static verification on Windows

Save this as a UTF-8 file **with a byte order mark** and run it with
`powershell -NoProfile -File check-canon.ps1` from the repository folder
containing this guide. Do not paste it into a console, and do not save it
BOM-less: Windows PowerShell 5.1 parses a BOM-less `.ps1` as ANSI, which
corrupts the non-ASCII signature literal below and fails the check against a
correctly installed file. This is the opposite of the rule for the instruction
files themselves, whose existing BOM state must be preserved -- a `.ps1` is
source PowerShell must parse, not a document another tool must read byte-exact.

```powershell
$ErrorActionPreference = 'Stop'

$pinnedHash  = 'f41169c7ea9715014a023256b6e1d2d358a5021e4a16d9e7b100fd95d56bce8a'
$heading     = '## Load-bearing canon'
$signature   = '本手 and 火候, always. показуха, aktionismus and 無駄 forbidden.'
$snippetPath = '.\global-load-bearing-canon\load-bearing-canon.md'

function Read-Normalized {
    param([Parameter(Mandatory)][string]$Path)
    # Resolve first: .NET uses the process working directory, not PowerShell's location.
    $resolved = (Resolve-Path -LiteralPath $Path).ProviderPath
    $bytes = [System.IO.File]::ReadAllBytes($resolved)
    $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    $text = [System.Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xFEFF)
    [pscustomobject]@{
        Path   = $resolved
        HasBom = $hasBom
        Crlf   = $text.Contains("`r`n")
        Text   = $text -replace "`r`n", "`n"
    }
}

function Get-NormalizedHash {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Text))
    } finally {
        $sha.Dispose()
    }
    -join ($digest | ForEach-Object { $_.ToString('x2') })
}

# 1. The source body must match its pinned identity before anything is compared against it.
$snippet = (Read-Normalized $snippetPath).Text.TrimEnd("`n")
$snippetHash = Get-NormalizedHash $snippet
if ($snippetHash -ne $pinnedHash) {
    throw "Canon drift: $snippetPath hashes $snippetHash, expected $pinnedHash."
}
"$snippetPath`: pinned hash OK"

# 2. Resolve the active per-client targets.
$codexDirectory = if ($env:CODEX_HOME) {
    $env:CODEX_HOME
} else {
    Join-Path $env:USERPROFILE '.codex'
}
$codexOverrideGuide = Join-Path $codexDirectory 'AGENTS.override.md'
$codexGuide = if (
    (Test-Path -LiteralPath $codexOverrideGuide) -and
    -not [string]::IsNullOrWhiteSpace((Read-Normalized $codexOverrideGuide).Text)
) {
    $codexOverrideGuide
} else {
    Join-Path $codexDirectory 'AGENTS.md'
}
$claudeDirectory = if ($env:CLAUDE_CONFIG_DIR) {
    $env:CLAUDE_CONFIG_DIR
} else {
    Join-Path $env:USERPROFILE '.claude'
}
$claudeGuide = Join-Path $claudeDirectory 'CLAUDE.md'

# 3. Each target needs exactly one body, one heading, and one signature line.
#    The signature-line count is what catches a copy relocated under another heading.
foreach ($guide in @($codexGuide, $claudeGuide)) {
    $file = Read-Normalized $guide
    foreach ($probe in @(
        @{ Name = 'body';           Value = $snippet },
        @{ Name = 'heading';        Value = $heading },
        @{ Name = 'signature line'; Value = $signature }
    )) {
        $count = ([regex]::Matches($file.Text, [regex]::Escape($probe.Value))).Count
        if ($count -ne 1) {
            throw "$guide contains $count copies of the canon $($probe.Name); expected 1."
        }
    }
    $firstH2 = ([regex]::Match($file.Text, '(?m)^##\s.*$')).Value
    $placement = if ($firstH2 -eq $heading) { 'first ## heading' } else { "below '$firstH2'" }
    $bom = if ($file.HasBom) { 'BOM' } else { 'no BOM' }
    $eol = if ($file.Crlf) { 'CRLF' } else { 'LF' }
    "$guide`: OK ($bom, $eol, $placement)"
}

# 4. VS Code loads ~/.copilot/instructions by glob, so any *.instructions.md file
#    carrying the canon is an active source - including a backup left in that folder.
$copilotFolder = Join-Path $env:USERPROFILE '.copilot\instructions'
$copilotGuide  = Join-Path $copilotFolder 'load-bearing-canon.instructions.md'
if (Test-Path -LiteralPath $copilotFolder) {
    $stray = @(
        Get-ChildItem -LiteralPath $copilotFolder -File |
            Where-Object { $_.Name -like '*.instructions.md' -and $_.FullName -ne $copilotGuide } |
            Where-Object { (Read-Normalized $_.FullName).Text.Contains($signature) }
    )
    if ($stray.Count -gt 0) {
        throw "Extra active Copilot instruction files carry the canon: $($stray.FullName -join ', ')"
    }
}
if (Test-Path -LiteralPath $copilotGuide) {
    $copilot = (Read-Normalized $copilotGuide).Text
    if ($copilot -notmatch '(?ms)\A---\s*\napplyTo:\s*"\*\*"\s*\n---') {
        throw "$copilotGuide does not start with applyTo: `"**`" front matter at column 0."
    }
    if (([regex]::Matches($copilot, [regex]::Escape($snippet))).Count -ne 1) {
        throw "$copilotGuide does not contain exactly one canon body."
    }
    "$copilotGuide`: OK"
}
```

Expected result: a `pinned hash OK` line for the source body, then one `OK` line
per target reporting its BOM state, newline convention, and whether the canon is
that file's first `##` heading. The script exits non-zero and names the offender
when the source body fails the pin, when a target holds zero or more than one
body, heading, or signature line, or when an extra `*.instructions.md` file in
`~/.copilot/instructions` also carries the canon. Placement below another
heading is reported, not fatal. The Copilot block is conditional: no `.copilot`
file is expected when VS Code reuses the global Claude file.

The signature-line count is the check that catches a copy moved under a
different heading, which a body-only substring count silently accepts. No string
check detects a semantic paraphrase that shares no exact text; the pinned hash
is the control for that.

## Fresh-session verification

1. Start a new Codex task from any repository and ask:

   > Quote the first sentence under `Load-bearing canon` from your loaded
   > instructions.

   Expected answer: `本手 and 火候, always.`

2. For Claude Code, either start a new session, run `/context`, and confirm the
   user-level `CLAUDE.md` appears under memory files; or take the same proof
   non-interactively, which needs no GUI and can run in a script:

   ```bash
   claude -p "Quote verbatim the first sentence under the heading 'Load-bearing canon' in your loaded instructions. Output only that sentence."
   ```

   Expect the same sentence. Claude Code builds its memory chain per session, so
   a fresh `claude -p` run is a genuine fresh-session check rather than a static
   one. Prefer it over labelling the Claude client unverified.

   This path needs an authenticated CLI. The headless invocation uses its own
   stored credentials, which can be expired even while an interactive session
   works; `API Error: 401 OAuth access token has expired` means re-authenticate
   and re-run, not that the canon is missing. Report the 401 as the reason the
   check did not run — never downgrade it to a static check and call it proof.

3. In VS Code Chat, right-click the Chat view and select **Diagnostics**.
   Confirm that exactly one of `~/.claude/CLAUDE.md` or the native `.copilot`
   instruction file is loaded without errors. Submit a GitHub Copilot Chat
   request, expand **References**, and confirm the selected file was used. Ask
   the same question and expect the same sentence. A fresh chat makes this
   verification easier to interpret, but is not required to load a saved file.

If the file is missing from a new session:

- Confirm Codex is using the expected `CODEX_HOME`.
- Check for a non-empty global Codex `AGENTS.override.md`.
- Confirm Claude Code is using the expected `CLAUDE_CONFIG_DIR` and that user
  settings were not excluded by the caller.
- In VS Code, inspect `chat.instructionsFilesLocations`,
  `chat.includeApplyingInstructions`, and `chat.useClaudeMdFile`, then recheck
  **Diagnostics** and the response **References**.
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

VS Code combines applicable instruction files. Personal instructions have
higher priority than repository and organization instructions, but no specific
order is guaranteed among multiple project instruction files. Avoid duplicate
or conflicting guidance instead of relying on discovery order.

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
- No native `.copilot` file was installed or validated. Verify VS Code through
  **Diagnostics** and response **References** before claiming user-path proof.

## Validation record for the September 2, 2026 instruction revision

The install instructions were reviewed and corrected; the canonical body and the
two installed files were not modified. Results:

- The pinned normalized hash was computed from the canonical body and matches
  `f41169c7…`, 427 bytes.
- The rewritten verification script was run against both live targets and
  passed: `AGENTS.md` and `CLAUDE.md` each hold one body, one heading, and one
  signature line, are BOM-less LF, and carry the canon as their first `##`.
- The script's gates were tested against a sandbox rather than assumed. Canon
  drift, a duplicated body, a signature line relocated under another heading,
  and a `*.instructions.md` backup left in `~/.copilot/instructions` were each
  injected and each correctly rejected; placement below another heading was
  correctly reported without failing.
- A BOM-less copy of the script was confirmed to fail against a correctly
  installed file under Windows PowerShell 5.1, which parses BOM-less `.ps1` as
  ANSI and corrupts the signature literal. The script must be saved with a BOM.
- `claude -p` fresh-session proof **passed**. The first attempt returned
  `API Error: 401 OAuth access token has expired`; after re-authenticating the
  CLI, a fresh headless session answered `本手 and 火候, always.` A 401 on this
  check means re-authenticate and re-run — it is not evidence that the canon is
  missing.
- Codex live proof is **blocked, not unattempted**.
  `codex exec --skip-git-repo-check` reached the service and failed with
  `The 'gpt-5.6-terra' model requires a newer version of Codex.` The installed
  CLI is too old for its configured model, so it cannot open a session to be
  asked. The same run also logged a model-list refresh failure ("unknown
  variant max") from the same version skew. Codex static installation is
  unaffected and still passes. Re-run this check after upgrading the Codex CLI.
- VS Code live proof was not attempted; it requires the Diagnostics GUI step.

## Rollback

Remove the complete `## Load-bearing canon` section from each user-level file,
preserving all surrounding content, then start new Codex and Claude Code
sessions. If the native `.copilot` option was installed, remove that instruction
file; when VS Code reuses `~/.claude/CLAUDE.md`, there is no separate Copilot
artifact to roll back. No hook, cache, repository, or application rollback is
required.

## Official references

- [Codex custom instructions with AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Codex IDE configuration](https://learn.chatgpt.com/docs/developer-settings?surface=ide)
- [Claude Code instructions and memory](https://code.claude.com/docs/en/memory)
- [Claude Code configuration directory](https://code.claude.com/docs/en/claude-directory)
- [VS Code custom instructions](https://code.visualstudio.com/docs/agent-customization/custom-instructions)
- [VS Code AI settings reference](https://code.visualstudio.com/docs/agents/reference/ai-settings)
- [GitHub Copilot instructions in VS Code](https://docs.github.com/en/copilot/how-tos/configure-custom-instructions-in-your-ide/add-repository-instructions-in-your-ide?tool=vscode)
- [GitHub Copilot custom-instruction support](https://docs.github.com/en/copilot/reference/custom-instructions-support)
