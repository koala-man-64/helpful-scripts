#requires -Version 5.1
<#
.SYNOPSIS
Preview or terminate orphaned Windows development helper processes.
.DESCRIPTION
Missing parents are evidence of orphaning, not proof that a process is unused.
Defaults to preview. -Kill opts into termination; -ProcessId limits the selection.
Only same-user, same-session helpers are eligible. Live application-owned trees,
the calling process and its ancestors, and unknown process identities are excluded.
.EXAMPLE
.\Stop-OrphanedHelpers.ps1
.EXAMPLE
.\Stop-OrphanedHelpers.ps1 -Kill -ProcessId 10820 -WhatIf
.EXAMPLE
.\Stop-OrphanedHelpers.ps1 -Kill -ProcessId 10820
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Kill,
    [ValidateRange(1, 2147483647)]
    [int[]]$ProcessId,
    [ValidateRange(1, 10080)]
    [int]$MinimumAgeMinutes = 10
)

function Get-OrphanHelperCandidate {
    param(
        [object[]]$Snapshot,
        [int]$CallerId,
        [int]$SessionId,
        [datetime]$Now,
        [int]$MinimumAgeMinutes
    )
    # Application executables and interactive shells are excluded; CMD commonly
    # wraps helper servers and is the only eligible shell.
    $names = @('node.exe', 'node_repl.exe', 'python.exe', 'pythonw.exe',
        'agentcoord-mcp.exe', 'docker-mcp.exe', 'cmd.exe', 'uv.exe', 'uvx.exe')
    $byId = @{}
    foreach ($item in $Snapshot) { $byId[[int]$item.ProcessId] = $item }
    $protected = @{}
    $cursor = $byId[$CallerId]
    while ($null -ne $cursor -and -not $protected.ContainsKey([int]$cursor.ProcessId)) {
        $protected[[int]$cursor.ProcessId] = $true
        $parent = $byId[[int]$cursor.ParentProcessId]
        if ($null -eq $parent -or $null -eq $parent.CreationDate -or
            $null -eq $cursor.CreationDate -or $parent.CreationDate -gt $cursor.CreationDate) { break }
        $cursor = $parent
    }
    foreach ($item in $Snapshot) {
        $cursor = $item
        $visited = @{}
        $depth = 0
        while ($null -ne $cursor) {
            $id = [int]$cursor.ProcessId
            if ($id -le 4 -or $protected.ContainsKey($id) -or $visited.ContainsKey($id) -or
                $names -notcontains $cursor.Name -or $cursor.SessionId -ne $SessionId -or
                $null -eq $cursor.CreationDate -or
                ($Now - $cursor.CreationDate).TotalMinutes -lt $MinimumAgeMinutes) { break }
            $visited[$id] = $true
            $parent = $byId[[int]$cursor.ParentProcessId]
            if ($cursor.ParentProcessId -le 0) { break }
            if ($null -eq $parent -or ($null -ne $parent.CreationDate -and
                    $parent.CreationDate -gt $cursor.CreationDate)) {
                [pscustomobject]@{
                    ProcessId = [int]$item.ProcessId
                    Name = $item.Name
                    ParentProcessId = [int]$item.ParentProcessId
                    CreationDate = $item.CreationDate
                    RootId = $id
                    Depth = $depth
                    MemoryMB = [math]::Round($item.WorkingSetSize / 1MB, 1)
                    Reason = if ($null -eq $parent) { 'Missing ancestor parent' } else { 'Ancestor parent PID reused' }
                }
                break
            }
            if ($null -eq $parent.CreationDate) { break }
            $cursor = $parent
            $depth++
        }
    }
}

function Test-HelperOwner {
    param($Process, [string]$Sid)
    try {
        $owner = Invoke-CimMethod -InputObject $Process -MethodName GetOwnerSid -ErrorAction Stop
        return ($owner.ReturnValue -eq 0 -and $owner.Sid -eq $Sid)
    }
    catch { return $false }
}

function Invoke-OrphanHelperCleanup {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param([switch]$Kill, [int[]]$ProcessId, [int]$MinimumAgeMinutes = 10)
    $ErrorActionPreference = 'Stop'
    $caller = Get-Process -Id $PID
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $snapshot = @(Get-CimInstance Win32_Process)
    $candidates = @(Get-OrphanHelperCandidate -Snapshot $snapshot -CallerId $PID `
        -SessionId $caller.SessionId -Now (Get-Date) -MinimumAgeMinutes $MinimumAgeMinutes |
        Where-Object { -not $ProcessId -or $_.ProcessId -in $ProcessId } |
        Sort-Object Depth -Descending)
    foreach ($candidate in $candidates) {
        $result = $candidate | Select-Object ProcessId, Name, ParentProcessId, RootId, MemoryMB, Reason,
            @{Name = 'Status'; Expression = { 'Candidate' }}
        $handleProcess = $null
        try {
            # Refresh the whole ancestry before EACH termination. Never kill a new
            # occupant of a PID selected from an older snapshot.
            $fresh = @(Get-CimInstance Win32_Process)
            $current = $fresh | Where-Object { $_.ProcessId -eq $candidate.ProcessId }
            $eligible = @(Get-OrphanHelperCandidate -Snapshot $fresh -CallerId $PID `
                -SessionId $caller.SessionId -Now (Get-Date) -MinimumAgeMinutes $MinimumAgeMinutes |
                Where-Object { $_.ProcessId -eq $candidate.ProcessId -and
                    $_.CreationDate -eq $candidate.CreationDate })
            if (-not $eligible -or -not (Test-HelperOwner -Process $current -Sid $sid)) {
                $result.Status = 'Skipped: changed or owner unavailable'
            }
            elseif ($Kill -and $PSCmdlet.ShouldProcess("$($candidate.Name) PID $($candidate.ProcessId)", 'Terminate orphan helper')) {
                $handleProcess = [System.Diagnostics.Process]::GetProcessById($candidate.ProcessId)
                # Open and retain the OS handle before identity validation. Kill()
                # uses this object, not a fresh PID lookup (PID reuse protection).
                $null = $handleProcess.Handle
                $delta = [math]::Abs(($handleProcess.StartTime.ToUniversalTime() -
                    $candidate.CreationDate.ToUniversalTime()).Ticks)
                # CIM timestamps have microsecond precision (10 ticks).
                if ($delta -ge 10) { throw 'Process identity changed before termination.' }
                $handleProcess.Kill()
                if ($handleProcess.WaitForExit(5000)) { $result.Status = 'Stopped' }
                else { $result.Status = 'Termination requested; exit unconfirmed' }
            }
            elseif ($Kill) { $result.Status = 'Not stopped (WhatIf or declined)' }
        }
        catch {
            $result.Status = "Skipped/error: $($_.Exception.Message)"
            Write-Warning "PID $($candidate.ProcessId): $($_.Exception.Message)"
        }
        finally { if ($null -ne $handleProcess) { $handleProcess.Dispose() } }
        $result
    }
    if (-not $candidates) { Write-Verbose 'No eligible orphan helper processes found.' }
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-OrphanHelperCleanup @PSBoundParameters
}
