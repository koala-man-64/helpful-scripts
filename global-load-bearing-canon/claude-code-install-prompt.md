# Claude Code handoff prompt

Give Claude Code access to this entire folder, then paste the prompt below.
Run it from a checkout that contains both this file and the sibling
[load-bearing-canon.md](load-bearing-canon.md). The prompt edits the active
user's local configuration; it does not edit this source folder.

The prompt installs the canon for **Codex and Claude Code only**. VS Code/GitHub
Copilot Chat is inspected and reported but never written, because its only
authoritative proof-of-loading is a GUI action no command-line agent can
perform. A rule an agent can never satisfy is not a safeguard; it is dead text
that reads as live. See
[VS Code and GitHub Copilot Chat](README.md#vs-code-and-github-copilot-chat)
for the human-operator procedure.

## Prompt

```text
Apply the global load-bearing canon for the current local user across Codex and
Claude Code. Inspect and report VS Code/GitHub Copilot Chat; never write it.

Authoritative source

- The canonical body is the `load-bearing-canon.md` beside the
  `claude-code-install-prompt.md` you were given. Its pinned identity is:

    normalized SHA-256: f41169c7ea9715014a023256b6e1d2d358a5021e4a16d9e7b100fd95d56bce8a
    normalized length:  427 bytes

  "Normalized" means read as UTF-8, strip a leading BOM, replace CRLF with LF,
  and strip trailing newlines. The hash is the integrity control, not the file
  path. Verify it before using the body. Do not paraphrase, translate,
  regenerate, or embed a second maintained copy.
- Locate every `claude-code-install-prompt.md` in the accessible checkout and
  hash each sibling `load-bearing-canon.md`. Multiple copies are expected and
  are not an error: git worktrees, nested checkouts, and clones all duplicate
  this folder, so a uniqueness check would abort in ordinary repositories.
  - If at least one candidate matches the pinned hash, select it. Matching
    candidates are byte-identical, so choose deterministically: prefer a path
    containing no `worktrees` segment, then take the lexicographically first.
    Report every path found and which one you selected.
  - If candidates exist but none matches the pinned hash, stop and report canon
    drift, listing each path with its actual hash. Never install an unpinned
    body.
  - If no candidate exists, or a file is not valid UTF-8, stop and report the
    blocker without changing anything.

Scope and safety

- This is a local user-configuration change, not a repository or application
  change.
- Preserve every unrelated instruction already present in a target file.
- Do not change repository `AGENTS.md`, `CLAUDE.md`, `.github` instructions,
  skills, agents, hooks, hook matchers, permissions, machine reason codes,
  contracts, schemas, application files, VS Code settings, or anything under
  `~/.copilot`.
- Do not create commits, branches, pull requests, or tracking items for these
  user-local edits.
- Work only in the current operating environment. Windows, WSL, containers,
  remote hosts, and other user profiles have separate homes and must be handled
  by separate runs.

Backups

- Before changing any non-empty target, create a timestamped recoverable backup
  and report its path. Do not expose the file contents in the final report.
- Write backups to a dedicated directory outside every instruction-discovery
  path: `~/.canon-backups/<UTC timestamp>/`. Never write a backup into
  `~/.codex`, `~/.claude`, or `~/.copilot/instructions`.
- A backup filename must never end in `.instructions.md`, nor be named
  `AGENTS.md`, `AGENTS.override.md`, or `CLAUDE.md`. VS Code discovers
  `~/.copilot/instructions/*.instructions.md` by glob, so a backup that keeps
  that suffix silently becomes a second active instruction source — the exact
  duplicate-source condition this procedure forbids.

Preflight

1. Resolve and report the current platform and absolute user-home/profile path.
2. Resolve the Codex configuration directory from `CODEX_HOME`, falling back to
   `~/.codex`.
3. In that directory, select a non-empty `AGENTS.override.md` when present;
   otherwise select `AGENTS.md`. A non-empty global override is selected instead
   of `AGENTS.md`. Report the selected active Codex guide and any inactive guide
   that already contains the canon.
4. Resolve the Claude Code configuration directory from `CLAUDE_CONFIG_DIR`,
   falling back to `~/.claude`, and select its `CLAUDE.md`.
5. Report the Claude Code target and VS Code's documented user-home
   `~/.claude/CLAUDE.md` as separate absolute paths. They differ when
   `CLAUDE_CONFIG_DIR` is set; never infer that VS Code follows that variable.
6. Read, without changing, `~/.copilot/instructions/` and the applicable VS Code
   user/profile and workspace settings for `chat.useClaudeMdFile`,
   `chat.instructionsFilesLocations`, and `chat.includeApplyingInstructions`.
   Treat an absent `chat.useClaudeMdFile` as the documented default of enabled.
   This is reporting input only and authorizes no write.
7. For each write target, record before editing: whether it exists, its size,
   its SHA-256, whether it begins with a UTF-8 BOM, and its predominant newline
   convention.
8. Count each of these in every candidate target, comparing on normalized text:
   - the exact canonical body;
   - the exact heading line `## Load-bearing canon`;
   - the canon signature line, which is the body's first sentence.
   Each count must be 0 (not installed) or 1 (already installed), and the three
   must agree. Any count above 1, or a disagreeing set, is a conflict: stop and
   report it. Do not guess, rewrite, or delete.

   These string checks catch duplication, relocation under a different heading,
   and modification of the body. They do not detect a semantic paraphrase that
   shares no exact string. The pinned hash is the control for that. Do not claim
   paraphrase detection.

Implementation

1. Codex: ensure the selected active Codex guide contains exactly one unchanged
   copy of the canonical body. Do not add the canon to both `AGENTS.md` and
   `AGENTS.override.md`; only the selected active guide is a target.
2. Claude Code: ensure the resolved `CLAUDE.md` contains exactly one unchanged
   copy of the canonical body.
3. Placement on first insertion: the canon must become the file's first `##`
   heading. Insert it after the opening `#` title and any paragraphs that
   immediately follow it, and before the first existing `##` heading. If the file
   has no `#` title, insert at the start. If the file is empty or newly created,
   the body becomes its entire contents.
4. If a target already contains exactly one copy, make no change, even when it
   sits below another `##` heading. Report that placement as an observation.
   Idempotence outranks placement; never relocate an existing correct body.

Encoding and newlines

- Preserve an existing target's BOM presence and predominant newline convention.
  Write a new file as UTF-8 without a BOM using the platform's normal newline.
- Do not use PowerShell 5.1 `Set-Content`, `Add-Content`, `Out-File`, or `>` for
  these writes. Its `-Encoding utf8` always emits a BOM and offers no BOM-less
  mode, so it silently adds a BOM to a BOM-less file while still passing a
  substring-based canon check. Write through the .NET API instead:

    $encoding = New-Object System.Text.UTF8Encoding($false)   # $true to keep an existing BOM
    [System.IO.File]::WriteAllText($path, $text, $encoding)

  On any platform an equivalent explicit UTF-8, BOM-controlled, newline-
  preserving write is acceptable. State which mechanism you used.
- Read back and confirm all five terms round-trip exactly.

Preservation and idempotence

- Create missing parent directories and target files only when they are the
  selected targets.
- Preserve unrelated content and keep the canonical body contiguous and
  unchanged.
- A second run with the same configuration must make no file changes and must
  report the same selected targets.

Static validation

1. The selected active Codex guide contains exactly one normalized exact
   canonical body, one `## Load-bearing canon` heading, and one signature line.
2. The active Claude Code `CLAUDE.md` satisfies the same three counts.
3. BOM state and predominant newline convention match what preflight recorded
   for each pre-existing target. Report the before and after BOM bytes
   explicitly; never infer BOM state from a successful write.
4. Report whether the canon is the first `##` heading in each target.
5. Strict UTF-8 readback succeeds and all five terms are present.
6. No backup was written into any instruction-discovery directory, and
   `~/.copilot/instructions/` gained no `*.instructions.md` file from this run.
7. Compare pre-edit and post-edit file lists and hashes. Confirm that only the
   selected user-level targets and their reported backups changed.
8. Re-run the preflight and prove idempotence by showing that no further edit is
   required. Do not claim idempotence merely because the first write succeeded.

Live activation and proof

- No operating-system reboot or full application restart is required.
- Claude Code: prove this yourself rather than labelling it unverified. Claude
  Code builds its memory chain per session, so a fresh non-interactive run is a
  real, observable fresh session:

    claude -p "Quote verbatim the first sentence under the heading 'Load-bearing canon' in your loaded instructions. Output only that sentence."

  Expect exactly: 本手 and 火候, always.
  Report the exact command, its output, and pass or fail. If the CLI is missing,
  unauthenticated, or refuses, report that as the reason and fall back to asking
  the operator to run `/context` in a new session.
- Codex: an existing run does not reload its instruction chain. If a Codex CLI is
  available, run the equivalent one-shot check and report its output; otherwise
  label it `Unverified / Needs confirmation` and give the operator the exact
  question to ask in a new task.
- VS Code/GitHub Copilot Chat: you cannot verify this. Diagnostics is a GUI
  action. Report it as `Not attempted - human step` and emit the operator
  checklist from the README instead of guessing.
- Never infer that a client loaded a file from file existence, a settings value,
  a commit, or a static check.

Required final report

- platform and resolved home/profile;
- every canonical-source path found, each one's hash, which was selected, and
  whether it matched the pin;
- selected Codex and Claude Code target paths;
- files created or changed, backup paths, and pre/post SHA-256 hashes;
- normalized counts for body, heading, and signature line per target;
- BOM and newline state before and after, and the write mechanism used;
- whether the canon is the first `##` heading in each target;
- whether the second pass was a no-op;
- live proof per client, each reported independently as passed, failed,
  unverified, or not attempted, with the exact command and output where one ran;
- the VS Code operator checklist, left for a human;
- blockers or conflicts left untouched.
```

## Expected outcome

Claude should make only bounded user-level configuration edits to the Codex and
Claude Code guides, preserve all existing guidance, prove the Claude Code load
with a real fresh session, and hand VS Code verification to a human rather than
report an unreachable branch. A successful static edit is not proof that a
running client loaded the file.
