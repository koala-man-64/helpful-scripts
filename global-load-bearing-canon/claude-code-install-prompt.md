# Claude Code handoff prompt

Give Claude Code access to this entire folder, then paste the prompt below.
Run it from a checkout that contains both this file and the sibling
[load-bearing-canon.md](load-bearing-canon.md). The prompt edits the active
user's local configuration; it does not edit this source folder.

## Prompt

```text
Apply the global load-bearing canon for the current local user across Codex,
Claude Code, and, when applicable, VS Code/GitHub Copilot Chat.

Authoritative source

- Within the accessible checkout, locate exactly one file named
  `claude-code-install-prompt.md`. If zero or multiple matches exist, stop and
  report the ambiguity without changing anything.
- Resolve that file's absolute parent directory and read the sibling
  `load-bearing-canon.md` as UTF-8. Record its absolute path and SHA-256 hash.
  Its complete contents are the only canonical body. Do not paraphrase,
  translate, regenerate, or embed a second maintained copy.
- If the sibling file is unavailable or invalid UTF-8, stop without changing
  anything and report the blocker.

Scope and safety

- This is a local user-configuration change, not a repository or application
  change.
- Preserve every unrelated instruction already present in a target file.
- Before changing any non-empty target, create a timestamped recoverable backup
  and report its path. Do not expose the file contents in the final report.
- Do not change repository `AGENTS.md`, `CLAUDE.md`, `.github` instructions,
  skills, agents, hooks, hook matchers, permissions, machine reason codes,
  contracts, schemas, application files, or VS Code settings.
- Do not create commits, branches, pull requests, or tracking items for these
  user-local edits.
- Work only in the current operating environment. Windows, WSL, containers,
  remote hosts, and other user profiles have separate homes and must be handled
  by separate runs.

Preflight

1. Resolve and report the current platform and absolute user-home/profile path.
2. Resolve the Codex configuration directory from `CODEX_HOME`, falling back to
   `~/.codex`.
3. In that directory, select a non-empty `AGENTS.override.md` when present;
   otherwise select `AGENTS.md`. Report the selected active Codex guide and any
   inactive guide that already contains the canon.
4. Resolve the Claude Code configuration directory from `CLAUDE_CONFIG_DIR`,
   falling back to `~/.claude`, and select its `CLAUDE.md`.
5. Resolve and report the Claude Code target and VS Code's documented
   user-home `~/.claude/CLAUDE.md` as separate absolute paths. They may differ
   when `CLAUDE_CONFIG_DIR` is set; never infer that VS Code follows that
   environment variable.
6. Inspect the applicable VS Code user/profile and current-workspace settings
   for `chat.useClaudeMdFile`, `chat.instructionsFilesLocations`, and
   `chat.includeApplyingInstructions`. Treat an absent
   `chat.useClaudeMdFile` as the documented default of enabled, but report that
   live loading still needs Diagnostics/References proof.
   If the active VS Code profile or applicable workspace settings cannot be
   resolved uniquely, do not create or modify `.copilot`; complete only the
   Codex and Claude Code edits and report VS Code as
   `Unverified / Needs confirmation`.
7. Inspect, without changing, the VS Code user-home Claude file
   `~/.claude/CLAUDE.md` and the native Copilot target
   `~/.copilot/instructions/load-bearing-canon.instructions.md`.
8. Normalize CRLF and LF only for comparisons. Count exact canonical bodies in
   every candidate before writing. If a target contains a `## Load-bearing
   canon` section that is not the exact sibling body, or contains the exact body
   more than once, stop and report a conflict instead of guessing or deleting.

Implementation

1. Codex:
   - Ensure the selected active Codex guide contains exactly one unchanged copy
     of the canonical body as a standalone section near the top.
   - If it already contains exactly one copy, make no change.
   - Do not add the canon to both `AGENTS.md` and `AGENTS.override.md`; only the
     selected active global guide is a target.
2. Claude Code:
   - Ensure the resolved Claude Code `CLAUDE.md` contains exactly one unchanged
     copy of the canonical body as a standalone section near the top.
   - If it already contains exactly one copy, make no change.
3. VS Code/GitHub Copilot Chat:
   - Select exactly one active VS Code source for this canon.
   - Reuse the Claude file when `chat.useClaudeMdFile` is effective and that
     exact file is discoverable by VS Code.
   - If the Claude Code target differs from VS Code's documented user-home
     `~/.claude/CLAUDE.md`, never infer that VS Code discovers the custom
     location. Require Diagnostics proof before selecting or modifying a VS
     Code source. If Diagnostics proof is unavailable, complete only the Codex
     and Claude Code edits, leave `.copilot` untouched, and report VS Code as
     `Unverified / Needs confirmation`.
   - When the active Claude source already supplies the canon, do not add the
     canon to the native `.copilot` file.
   - Otherwise, use
     `~/.copilot/instructions/load-bearing-canon.instructions.md` only after
     confirming that its user-level folder is enabled and no other active VS
     Code instruction source contains the canon. Its complete format must be:

       ---
       applyTo: "**"
       ---

       <one unchanged copy of the sibling canonical body>

   - A Claude Code `CLAUDE.md` and native `.copilot` file may both contain the
     canon on disk only when they serve separate clients and the Claude file is
     not an active source for VS Code.
   - If both would be active in VS Code, stop and report a duplicate-source
     blocker. Do not silently delete or rewrite either source.

Preservation and idempotence

- Create missing parent directories and target files only when they are the
  selected targets.
- Preserve unrelated content and keep the canonical body contiguous and
  unchanged.
- Preserve an existing target's UTF-8 BOM presence and predominant newline
  convention. For a new file, use UTF-8 without a BOM and the current platform's
  normal newline convention.
- Write and read back UTF-8 so all five terms round-trip exactly.
- A second run with the same configuration must make no file changes and must
  report the same selected targets.

Static validation

1. Verify the selected active Codex guide contains exactly one normalized exact
   canonical body.
2. Verify the active Claude Code `CLAUDE.md` contains exactly one normalized
   exact canonical body.
3. Report physical files separately from active VS Code sources:
   - if VS Code reuses Claude, confirm the active Claude source has one copy and
     the native `.copilot` target has zero;
   - if VS Code uses the native source, confirm it has one copy with
     `applyTo: "**"`, while the Claude Code file retains its one copy for Claude
     Code but is not active for VS Code.
4. Verify strict UTF-8 readback and the exact presence of all five terms.
5. Compare pre-edit and post-edit file lists and hashes. Confirm that only the
   selected user-level targets and their reported backups changed.
6. Re-run the preflight and prove idempotence by showing that no further edit is
   required. Do not claim idempotence merely because the first write succeeded.

Live activation and proof

- No operating-system reboot or full application restart is required.
- Codex: start a new task, CLI run, or TUI session; an existing run should not
  be expected to reload its instruction chain.
- Claude Code: start a new conversation/session and use `/context` to confirm
  the selected user-level `CLAUDE.md` is loaded.
- VS Code/GitHub Copilot Chat: saved instructions are available to subsequent
  requests without a window reload. A fresh chat is optional for clean proof.
  Right-click the Chat view, select Diagnostics, and confirm exactly one canon
  source is loaded without errors. Submit a request and expand References to
  confirm the selected instruction file was used.
- If you cannot perform a live check, label it `Unverified / Needs
  confirmation`. Never infer user-path loading from file existence, settings,
  a commit, or a static check.

Required final report

- platform and resolved home/profile;
- canonical source absolute path and SHA-256 hash;
- selected Codex, Claude Code, and VS Code source paths;
- files created or changed, backup paths, and pre/post SHA-256 hashes;
- normalized exact-copy counts and UTF-8 result;
- whether the second pass was a no-op;
- static configuration result for each client;
- live Codex, Claude, and VS Code proof, each reported independently as passed,
  failed, or unverified;
- restart/session actions still needed;
- blockers or conflicts left untouched.
```

## Expected outcome

Claude should make only bounded user-level configuration edits, preserve all
existing guidance, and report any live verification it could not perform. A
successful static edit is not proof that a running client loaded the file.
