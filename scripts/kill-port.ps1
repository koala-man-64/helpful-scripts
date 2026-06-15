[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 5167
)

$connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
$processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique

if (-not $processIds -or $processIds.Count -eq 0) {
    Write-Host "No process is using TCP port $Port."
    return
}

$processes = foreach ($processId in $processIds) {
    try {
        Get-Process -Id $processId -ErrorAction Stop |
            Select-Object Id, ProcessName, Path
    } catch {
        [PSCustomObject]@{
            Id = $processId
            ProcessName = "(access denied or exited)"
            Path = ""
        }
    }
}

Write-Host "Killing processes on TCP port ${Port}:"
$processes | Format-Table -AutoSize

foreach ($processId in $processIds) {
    if ($PSCmdlet.ShouldProcess("PID $processId", "Stop process")) {
        try {
            Stop-Process -Id $processId -Force -ErrorAction Stop
            Write-Host "Stopped PID $processId"
        } catch {
            Write-Warning "Failed to stop PID ${processId}: $($_.Exception.Message)"
        }
    }
}
