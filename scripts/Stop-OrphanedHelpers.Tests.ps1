# Run with: pwsh -NoProfile -File scripts/Stop-OrphanedHelpers.Tests.ps1
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/Stop-OrphanedHelpers.ps1"
$now = Get-Date
function New-FixtureProcess {
    param([int]$Id, [int]$Parent, [string]$Name = 'node.exe', [int]$Age = 30, [int]$Session = 1)
    [pscustomobject]@{ ProcessId = $Id; ParentProcessId = $Parent; Name = $Name;
        CreationDate = $now.AddMinutes(-$Age); SessionId = $Session; WorkingSetSize = 1MB }
}
function Assert-Candidates {
    param([string]$Case, [object[]]$Rows, [int[]]$Expected = @(), [int]$Caller = 999)
    $actual = @(Get-OrphanHelperCandidate -Snapshot $Rows -CallerId $Caller -SessionId 1 `
        -Now $now -MinimumAgeMinutes 10 | Select-Object -ExpandProperty ProcessId | Sort-Object)
    if (($actual -join ',') -ne (($Expected | Sort-Object) -join ',')) {
        throw "$Case : expected $Expected; got $actual"
    }
    Write-Output "PASS: $Case"
}
Assert-Candidates 'missing parent' @((New-FixtureProcess 10 20)) @(10)
Assert-Candidates 'live app ancestor' @((New-FixtureProcess 10 20), (New-FixtureProcess 20 30 'codex.exe' 60))
Assert-Candidates 'reused parent PID' @((New-FixtureProcess 10 20), (New-FixtureProcess 20 30 'codex.exe' 5)) @(10)
Assert-Candidates 'child and orphan root' @((New-FixtureProcess 10 20), (New-FixtureProcess 20 30 'cmd.exe' 60)) @(10,20)
Assert-Candidates 'current caller ancestry' @((New-FixtureProcess 10 20), (New-FixtureProcess 20 30 'cmd.exe' 60)) @() 10
Assert-Candidates 'recent helper' @((New-FixtureProcess 10 20 'node.exe' 2))
Assert-Candidates 'another session' @((New-FixtureProcess 10 20 'node.exe' 30 2))
Assert-Candidates 'system PID' @((New-FixtureProcess 4 20))
Assert-Candidates 'zero parent' @((New-FixtureProcess 10 0))
Assert-Candidates 'non-helper' @((New-FixtureProcess 10 20 'notepad.exe'))
Assert-Candidates 'cycle' @((New-FixtureProcess 10 20), (New-FixtureProcess 20 10))
$unknown = New-FixtureProcess 20 30
$unknown.CreationDate = $null
Assert-Candidates 'unknown parent birth time' @((New-FixtureProcess 10 20), $unknown)

# Mock inventory/owner queries: preview, WhatIf, stale identity and owner rejection
# must never reach the native process handle lookup for this nonexistent PID.
$script:inventory = @(New-FixtureProcess 2000000000 2000000001)
$script:inventory[0].SessionId = (Get-Process -Id $PID).SessionId
function Get-CimInstance { $script:inventory }
$script:ownerAllowed = $true
function Test-HelperOwner { return $script:ownerAllowed }
$result = @(Invoke-OrphanHelperCleanup)
if ($result.Count -ne 1 -or $result[0].Status -ne 'Candidate') { throw 'Preview failed' }
Write-Output 'PASS: preview does not terminate'
$result = @(Invoke-OrphanHelperCleanup -Kill -WhatIf)
if ($result[0].Status -ne 'Not stopped (WhatIf or declined)') { throw 'WhatIf failed' }
Write-Output 'PASS: WhatIf does not terminate'
$result = @(Invoke-OrphanHelperCleanup -ProcessId 12345)
if ($result.Count -ne 0) { throw 'PID selection failed' }
Write-Output 'PASS: explicit PID filter'
$script:ownerAllowed = $false
$result = @(Invoke-OrphanHelperCleanup -Kill)
if ($result[0].Status -notlike 'Skipped:*') { throw 'Owner guard failed' }
Write-Output 'PASS: owner unavailable rejects termination'
$script:ownerAllowed = $true
$script:reads = 0
function Get-CimInstance {
    $script:reads++
    if ($script:reads -gt 1) { return @() }
    $script:inventory
}
$result = @(Invoke-OrphanHelperCleanup -Kill)
if ($result[0].Status -notlike 'Skipped:*') { throw 'Stale snapshot guard failed' }
Write-Output 'PASS: disappeared process rejected'
Write-Output 'All 17 tests passed.'
