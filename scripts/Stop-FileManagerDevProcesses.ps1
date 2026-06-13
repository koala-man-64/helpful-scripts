[CmdletBinding()]
param(
    [string[]]$Ports = @(),
    [string[]]$CommandLinePatterns = @(),
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs = @()
)

$normalizedPorts = [System.Collections.Generic.List[int]]::new()
$normalizedPatterns = [System.Collections.Generic.List[string]]::new()
$processIds = [System.Collections.Generic.HashSet[int]]::new()

function Add-PortValue {
    param(
        [System.Collections.Generic.List[int]]$List,
        [string]$Value
    )

    foreach ($candidate in ($Value -split ',')) {
        $trimmed = $candidate.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed)) {
            continue
        }

        $port = 0
        if ([int]::TryParse($trimmed, [ref]$port)) {
            $List.Add($port)
        }
        else {
            Write-Warning "Ignoring non-numeric port value '$trimmed'."
        }
    }
}

function Add-PatternValue {
    param(
        [System.Collections.Generic.List[string]]$List,
        [string]$Value
    )

    foreach ($candidate in ($Value -split ',')) {
        $trimmed = $candidate.Trim()
        if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
            $List.Add($trimmed)
        }
    }
}

function Add-ProcessId {
    param(
        [System.Collections.Generic.HashSet[int]]$Set,
        [int]$ProcessId
    )

    if ($ProcessId -gt 0 -and $ProcessId -ne $PID) {
        [void]$Set.Add($ProcessId)
    }
}

foreach ($port in $Ports) {
    Add-PortValue -List $normalizedPorts -Value $port
}

foreach ($pattern in $CommandLinePatterns) {
    Add-PatternValue -List $normalizedPatterns -Value $pattern
}

foreach ($argument in $RemainingArgs) {
    $port = 0
    if ([int]::TryParse($argument, [ref]$port)) {
        $normalizedPorts.Add($port)
    }
    else {
        Add-PatternValue -List $normalizedPatterns -Value $argument
    }
}

if ($normalizedPorts.Count -gt 0 -and (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
    foreach ($port in $normalizedPorts) {
        Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { Add-ProcessId -Set $processIds -ProcessId $_.OwningProcess }
    }
}

if ($normalizedPatterns.Count -gt 0) {
    Get-CimInstance Win32_Process -Filter "Name = 'dotnet.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $commandLine = $_.CommandLine
            if ([string]::IsNullOrWhiteSpace($commandLine)) {
                return $false
            }

            foreach ($pattern in $normalizedPatterns) {
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
