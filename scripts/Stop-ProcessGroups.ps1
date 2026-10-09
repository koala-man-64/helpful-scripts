<#
.SYNOPSIS
Lists running processes grouped by name (highest count first) and stops every
instance of the groups you pick by index.

.DESCRIPTION
Each round prints a numbered table of process names with their instance counts.
Enter one or more indexes (for example "3", "1,4,7", "2 5", or "3-6") to stop all
instances of those names, or "q" to exit. After each kill the list is rebuilt and
the new total process count is printed.

Windows-critical processes (System, csrss, lsass, ...) and this PowerShell host
are never stopped; they are marked with * in the list. Processes owned by other
users or elevated processes need an elevated shell, and failures are reported
per process rather than aborting the run.

.PARAMETER Force
Skip the "Stop N processes? (y/N)" confirmation.

.EXAMPLE
.\Stop-ProcessGroups.ps1

.EXAMPLE
.\Stop-ProcessGroups.ps1 -WhatIf
Shows what would be stopped without stopping anything.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Force
)

Set-StrictMode -Version Latest

# Stopping any of these crashes or destabilizes Windows.
$script:ProtectedNames = @(
    'Idle', 'System', 'Registry', 'Memory Compression', 'Secure System',
    'smss', 'csrss', 'wininit', 'winlogon', 'services', 'lsass'
)

function Get-ProcessGroupList {
    $groups = Get-Process |
        Group-Object -Property ProcessName |
        Sort-Object -Property @{ Expression = 'Count'; Descending = $true }, @{ Expression = 'Name' }

    $index = 0
    foreach ($group in $groups) {
        $index++
        [PSCustomObject]@{
            Index     = $index
            Name      = $group.Name
            Count     = $group.Count
            Protected = ($script:ProtectedNames -contains $group.Name) -or
                        ($group.Group.Id -contains $PID)
        }
    }
}

# Returns the selected indexes, or $null (after warning) if any token is invalid.
function ConvertTo-IndexList {
    param(
        [Parameter(Mandatory)][string]$Text,
        [Parameter(Mandatory)][int]$Max
    )

    $selected = [System.Collections.Generic.SortedSet[int]]::new()
    foreach ($token in ($Text -split '[,\s]+' | Where-Object { $_ })) {
        if ($token -match '^(\d+)-(\d+)$') {
            $from = [int]$Matches[1]
            $to = [int]$Matches[2]
        } elseif ($token -match '^\d+$') {
            $from = $to = [int]$token
        } else {
            Write-Warning "Not an index or range: '$token'"
            return $null
        }
        if ($from -gt $to -or $from -lt 1 -or $to -gt $Max) {
            Write-Warning "Out of range (valid: 1-$Max): '$token'"
            return $null
        }
        foreach ($i in $from..$to) { [void]$selected.Add($i) }
    }
    return , @($selected)
}

function Stop-ProcessGroup {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param([Parameter(Mandatory)][string]$Name)

    # Re-query so instances started since the list was built are included.
    $targets = @(Get-Process | Where-Object { $_.ProcessName -eq $Name -and $_.Id -ne $PID })
    $stopped = 0
    $failed = 0
    foreach ($process in $targets) {
        if (-not $PSCmdlet.ShouldProcess("$Name (PID $($process.Id))", 'Stop process')) { continue }
        try {
            Stop-Process -Id $process.Id -Force -Confirm:$false -ErrorAction Stop
            $stopped++
        } catch {
            $failed++
            Write-Warning "Could not stop $Name (PID $($process.Id)): $($_.Exception.Message)"
        }
    }
    Write-Host ("  {0}: stopped {1} of {2}" -f $Name, $stopped, $targets.Count)
}

function Invoke-ProcessGroupMenu {
    while ($true) {
        $list = @(Get-ProcessGroupList)
        $total = ($list | Measure-Object -Property Count -Sum).Sum

        Write-Host ''
        $list | Format-Table -AutoSize -Property Index, Name, Count,
            @{ Label = ''; Expression = { if ($_.Protected) { '*' } } } | Out-Host
        Write-Host ("{0} processes in {1} groups.  (* = protected, will not be stopped)" -f $total, $list.Count)

        $answer = (Read-Host 'Index(es) to kill (e.g. 3, 1,4,7, 2-5) or q to quit').Trim()
        if ($answer -in @('q', 'quit', 'exit')) { return }
        if (-not $answer) { continue }

        $indexes = ConvertTo-IndexList -Text $answer -Max $list.Count
        if ($null -eq $indexes) { continue }

        $chosen = @($list | Where-Object { $indexes -contains $_.Index })
        $skipped = @($chosen | Where-Object Protected)
        $chosen = @($chosen | Where-Object { -not $_.Protected })
        foreach ($item in $skipped) { Write-Warning "Skipping protected group: $($item.Name)" }
        if ($chosen.Count -eq 0) { continue }

        $instanceCount = ($chosen | Measure-Object -Property Count -Sum).Sum
        $names = ($chosen | ForEach-Object { "$($_.Name) x$($_.Count)" }) -join ', '
        if (-not $Force -and -not $WhatIfPreference) {
            $confirm = Read-Host "Stop $instanceCount processes ($names)? (y/N)"
            if ($confirm -notmatch '^(y|yes)$') { Write-Host 'Cancelled.'; continue }
        }

        foreach ($item in $chosen) { Stop-ProcessGroup -Name $item.Name }
        Start-Sleep -Milliseconds 500   # let the OS drop the exited processes

        $newTotal = @(Get-Process).Count
        Write-Host ("New process count: {0} (was {1})" -f $newTotal, $total)
    }
}

# Skip the menu when dot-sourced (lets tests load the functions).
if ($MyInvocation.InvocationName -ne '.') {
    Invoke-ProcessGroupMenu
}
