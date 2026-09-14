# Orphaned Windows helpers

Requires Windows PowerShell 5.1 or PowerShell 7 on Windows. No installation needed.

```powershell
# Preview only (objects can also be piped to Export-Csv or ConvertTo-Json).
.\scripts\Stop-OrphanedHelpers.ps1

# Preview the kill action for specific reviewed candidate PIDs.
.\scripts\Stop-OrphanedHelpers.ps1 -Kill -ProcessId 10820,12345 -WhatIf

# Stop those candidates, after fresh checks.
.\scripts\Stop-OrphanedHelpers.ps1 -Kill -ProcessId 10820,12345

# Stop all qualifying candidates.
.\scripts\Stop-OrphanedHelpers.ps1 -Kill
```

Replace the example PIDs with current preview results. `-Kill` is explicit opt-in
to termination; use `-Confirm` for per-process prompts. An orphan can still be
doing useful work. Termination is forceful and can lose its unfinished work.

The script finds helper trees whose original parent is missing or whose parent
PID has been reused (the current parent started after its child). Every process
on the path to the orphan root must be an eligible helper in the same Windows
session and at least ten minutes old (`-MinimumAgeMinutes` changes this).
The allowlist is Node, Node REPL, Python, agentcoord-mcp, docker-mcp, CMD, uv and
uvx. PowerShell, Bash, Codex, browsers, Docker itself and system processes are
excluded. CMD is included because it commonly wraps helper servers.

Only processes owned by the current Windows user are candidates for termination.
Unknown ownership, missing creation times, live application ancestors, and the
script's own process/ancestors are protected. Each selected process is rechecked
and stopped through a retained process handle after verifying its creation time.
Results report confirmed exit, skipped/error, or unconfirmed termination.

Descendants are processed before roots. A PID filter selects only those exact
PIDs, not their entire trees. Unlisted descendants are never forcibly traversed
or terminated. Processes created after the initial inventory are not selected.
Run preview again to see remaining eligible helpers.

This does **not** diagnose unused helpers still owned by a running Codex process.
A large helper count is not proof of orphaning. Close completed tasks or restart
their owning application when suitable, then rerun preview.

Tests (mocked inventory; do not terminate real processes):

```powershell
pwsh -NoProfile -File .\scripts\Stop-OrphanedHelpers.Tests.ps1
```
