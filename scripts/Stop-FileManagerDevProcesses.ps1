[CmdletBinding()]
param(
    [int[]]$Ports = @(),
    [string[]]$CommandLinePatterns = @()
)

$processIds = [System.Collections.Generic.HashSet[int]]::new()

function Add-ProcessId {
    param(
        [System.Collections.Generic.HashSet[int]]$Set,
        [int]$ProcessId
    )

    if ($ProcessId -gt 0 -and $ProcessId -ne $PID) {
        [void]$Set.Add($ProcessId)
    }
}

if ($Ports.Count -gt 0 -and (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
    foreach ($port in $Ports) {
        Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { Add-ProcessId -Set $processIds -ProcessId $_.OwningProcess }
    }
}

if ($CommandLinePatterns.Count -gt 0) {
    Get-CimInstance Win32_Process -Filter "Name = 'dotnet.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $commandLine = $_.CommandLine
            if ([string]::IsNullOrWhiteSpace($commandLine)) {
                return $false
            }

            foreach ($pattern in $CommandLinePatterns) {
                if (-not [string]::IsNullOrWhiteSpace($pattern) -and $commandLine -match $pattern) {
                    return $true
                }
            }

            return $false
        } |
        ForEach-Object { Add-ProcessId -Set $processIds -ProcessId $_.ProcessId }
}

foreach ($processId in ($processIds | Sort-Object)) {
    try {
        $process = Get-Process -Id $processId -ErrorAction Stop
        Write-Host "Stopping $($process.ProcessName) process $processId."
        Stop-Process -Id $processId -Force -ErrorAction Stop
    }
    catch {
        Write-Warning "Could not stop process $processId. It may have already exited."
    }
}
