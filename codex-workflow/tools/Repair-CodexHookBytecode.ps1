#Requires -Version 5.1
<#
.SYNOPSIS
Preview or quarantine the four verified CPython 3.14 cache files from the
3a7c218 CodexWorkflowHooks release. No installed source or manifest edits.
Use only after the runtime owner has confirmed the cache-only diagnosis.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [string]$InstallStatePath,
    [string]$PythonExecutable = 'python',
    [string]$CodexHome,
    [string]$QuarantineRoot,
    [string]$ExpectedVersion = '0.6.3+sha.3a7c21839d428f2240a21c238b85947bb62b1b17',
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedManifestDigest = '9912fe39549f8673564dcb5ca45d1f767b86fed1bbe96d3f5fcd7710bde1ee5b',
    [switch]$Apply
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$cacheRelative = 'src/codex_workflow_hooks/__pycache__'
$expectedNames = @('__init__.cpython-314.pyc', 'models.cpython-314.pyc',
    'subagent_routing.cpython-314.pyc', 'utils.cpython-314.pyc')
$expectedEvents = @('SessionStart', 'UserPromptSubmit', 'SubagentStart',
    'PreToolUse', 'PostToolUse', 'Stop', 'SessionEnd')

function Assert-SameSet([object[]]$Actual, [object[]]$Expected, [string]$Label) {
    if ($Actual.Count -ne $Expected.Count -or
        ($Actual.Count -gt 0 -and @(Compare-Object -ReferenceObject $Expected -DifferenceObject $Actual -CaseSensitive).Count -ne 0)) {
        throw "Unexpected $Label; recovery refuses this state."
    }
}
function Resolve-SafeExisting([string]$Path, [bool]$Directory) {
    if (-not [IO.Path]::IsPathRooted($Path)) { throw "Absolute path required: $Path" }
    $full = [IO.Path]::GetFullPath($Path)
    $item = Get-Item -LiteralPath $full -Force
    if ([bool]$item.PSIsContainer -ne $Directory) { throw "Unexpected path type: $full" }
    $cursor = $item
    while ($null -ne $cursor) {
        if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Reparse point rejected: $($cursor.FullName)"
        }
        $parent = [IO.Path]::GetDirectoryName($cursor.FullName.TrimEnd('\'))
        if ([string]::IsNullOrEmpty($parent)) { break }
        $cursor = Get-Item -LiteralPath $parent -Force
    }
    return (Resolve-Path -LiteralPath $full).ProviderPath
}
function Test-Within([string]$Child, [string]$Parent) {
    return $Child.StartsWith($Parent.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
}
function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}
function Read-Doctor {
    $arguments = @('-B', $hookctl, 'doctor', '--json', '--data-dir', $dataDirectory)
    if ($CodexHome) { $arguments += @('--codex-home', $CodexHome) }
    $lines = @(& $PythonExecutable @arguments)
    $nativeExit = $LASTEXITCODE
    if ($nativeExit -notin @(0, 1)) { throw "Doctor failed to execute: exit $nativeExit" }
    $value = ($lines -join [Environment]::NewLine) | ConvertFrom-Json
    if (@($value.diagnostic_failures).Count -ne 0 -or
        $value.installation_state.valid -ne $true -or
        @($value.installation_state.failures).Count -ne 0 -or
        @($value.release.failures).Count -ne 0 -or
        @($value.release.invalid_manifest_entries).Count -ne 0 -or
        $value.release.version -cne $ExpectedVersion -or
        $value.release.manifest_digest -cne $ExpectedManifestDigest) {
        throw 'Doctor found a different installation, manifest, or tracked-file failure.'
    }
    return $value
}
function Assert-Healthy($Doctor) {
    Assert-SameSet @($Doctor.release.unexpected_paths) @() 'release extras'
    Assert-SameSet @($Doctor.owned_events) $expectedEvents 'owned hook events'
    if ($Doctor.release.valid -ne $true -or $Doctor.healthy -ne $true -or
        $Doctor.hook_configuration.valid -ne $true) {
        throw 'Post-recovery doctor did not confirm release and hook health.'
    }
}
function Assert-CacheOnly($Doctor) {
    $expectedPaths = @($cacheRelative + '/')
    $expectedPaths += @($expectedNames | ForEach-Object { "$cacheRelative/$_" })
    $actual = @($Doctor.release.unexpected_paths | ForEach-Object { $_.Replace('\', '/') })
    Assert-SameSet $actual $expectedPaths 'release extras'
    if ($Doctor.release.valid -ne $false) { throw 'Cache-only diagnosis no longer matches.' }
}
function Get-BytecodeInventory([string]$Directory) {
    $resolved = Resolve-SafeExisting $Directory $true
    $items = @(Get-ChildItem -LiteralPath $resolved -Force)
    Assert-SameSet @($items.Name) $expectedNames 'cache filenames'
    $hashes = [ordered]@{}
    foreach ($item in $items | Sort-Object Name) {
        if ($item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Non-regular cache entry rejected: $($item.FullName)"
        }
        $hashes[$item.Name] = Get-Sha256 $item.FullName
    }
    return $hashes
}
function Get-CacheInventory {
    $resolved = Resolve-SafeExisting $cachePath $true
    if ($resolved -ine $cachePath -or -not (Test-Within $resolved $releasePath)) {
        throw 'Cache escaped the verified release boundary.'
    }
    return Get-BytecodeInventory $resolved
}
function Invoke-SelfTest {
    $installedHookctl = Resolve-SafeExisting (Join-Path $releasePath 'hookctl.py') $false
    if (-not (Test-Within $installedHookctl $releasePath)) { throw 'Installed self-test escaped release boundary.' }
    $entrypointDigest = Get-Sha256 $installedHookctl
    $lines = @(& $PythonExecutable -B $installedHookctl self-test)
    $nativeExit = $LASTEXITCODE
    if ($nativeExit -ne 0) { throw "Self-test failed: exit $nativeExit; $($lines -join [Environment]::NewLine)" }
    if ((Get-Sha256 $installedHookctl) -cne $entrypointDigest) { throw 'Installed self-test entrypoint changed.' }
    return [ordered]@{ run = $true; exit_code = $nativeExit; path = $installedHookctl;
        sha256 = $entrypointDigest; output = $lines -join [Environment]::NewLine }
}

$previousBytecodeSetting = [Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', 'Process')
try {
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $SourceRoot = Resolve-SafeExisting $SourceRoot $true
    $hookctl = Resolve-SafeExisting (Join-Path $SourceRoot 'hookctl.py') $false
    $sourcePackage = Resolve-SafeExisting (Join-Path $SourceRoot 'src') $true
    if (-not $InstallStatePath) {
        $probe = "import sys; sys.path.insert(0,sys.argv[1]); from codex_workflow_hooks.cli import default_data_dir; print(str(default_data_dir() / 'install.json'))"
        $discovered = @(& $PythonExecutable -B -c $probe $sourcePackage)
        if ($LASTEXITCODE -ne 0 -or $discovered.Count -ne 1) { throw 'Install-state discovery failed.' }
        $InstallStatePath = $discovered[0].Trim()
    }
    $InstallStatePath = Resolve-SafeExisting $InstallStatePath $false
    $stateHash = Get-Sha256 $InstallStatePath
    $install = [IO.File]::ReadAllText($InstallStatePath) | ConvertFrom-Json
    if ($install.schema_version -ne 1 -or $install.version -cne $ExpectedVersion -or
        $install.manifest_digest -cne $ExpectedManifestDigest) {
        throw 'Install-state schema, version, or manifest binding differs from the approved recovery.'
    }
    $releasePath = Resolve-SafeExisting $install.release_path $true
    $dataDirectory = Resolve-SafeExisting $install.data_dir $true
    if ($InstallStatePath -ine (Join-Path $dataDirectory 'install.json')) {
        throw 'Explicit install state does not belong to its declared data directory.'
    }
    if ($CodexHome) { $CodexHome = Resolve-SafeExisting $CodexHome $true }
    $cachePath = [IO.Path]::GetFullPath((Join-Path $releasePath $cacheRelative))
    if (-not (Test-Within $cachePath $releasePath)) { throw 'Invalid cache boundary.' }

    $before = Read-Doctor
    if ($before.release.valid -eq $true) {
        Assert-Healthy $before
        $selfTest = [ordered]@{ run = $false; exit_code = $null }
        if ($Apply) { $selfTest = Invoke-SelfTest }
        [pscustomobject]@{ status = 'already_clean'; applied = $false; release = $releasePath;
            doctor = $before; self_test = $selfTest } | ConvertTo-Json -Depth 20
        return
    }
    Assert-CacheOnly $before
    $cacheHashes = Get-CacheInventory
    if (-not $QuarantineRoot) {
        $QuarantineRoot = Join-Path (Split-Path -Parent (Split-Path -Parent $releasePath)) 'recovery-quarantine'
    }
    if (-not [IO.Path]::IsPathRooted($QuarantineRoot)) { throw 'Quarantine root must be absolute.' }
    $QuarantineRoot = [IO.Path]::GetFullPath($QuarantineRoot).TrimEnd('\')
    if ($QuarantineRoot -ieq $releasePath -or (Test-Within $QuarantineRoot $releasePath)) {
        throw 'Quarantine must be outside the installed release.'
    }
    if (Test-Path -LiteralPath $QuarantineRoot) {
        $QuarantineRoot = Resolve-SafeExisting $QuarantineRoot $true
    } else {
        $null = Resolve-SafeExisting (Split-Path -Parent $QuarantineRoot) $true
    }
    $caseName = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' + [guid]::NewGuid().ToString('N')
    $caseDirectory = Join-Path $QuarantineRoot $caseName
    $destination = Join-Path $caseDirectory '__pycache__'
    $plan = [ordered]@{ status = 'preview'; applied = $false; release = $releasePath;
        source = $cachePath; quarantine = $destination; files = $cacheHashes;
        version = $ExpectedVersion; manifest_digest = $ExpectedManifestDigest;
        install_state_sha256 = $stateHash.ToLowerInvariant(); doctor = $null;
        quarantine_verified = $false; self_test = [ordered]@{ run = $false; exit_code = $null } }
    if (-not $Apply) { $plan | ConvertTo-Json -Depth 20; return }

    if (-not (Test-Path -LiteralPath $QuarantineRoot)) {
        $null = New-Item -ItemType Directory -Path $QuarantineRoot
    }
    $null = Resolve-SafeExisting $QuarantineRoot $true
    $null = New-Item -ItemType Directory -Path $caseDirectory
    $caseDirectory = Resolve-SafeExisting $caseDirectory $true
    if (-not (Test-Within $caseDirectory $QuarantineRoot) -or
        (Test-Within $caseDirectory $releasePath)) { throw 'Quarantine case boundary changed.' }
    $receiptPath = Join-Path $caseDirectory 'recovery.json'
    $plan | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8

    Assert-CacheOnly (Read-Doctor)
    if ((Get-Sha256 $InstallStatePath) -cne $stateHash -or
        ((Get-CacheInventory | ConvertTo-Json -Compress) -cne ($cacheHashes | ConvertTo-Json -Compress))) {
        throw 'Installation or cache bytes changed during recovery preparation.'
    }
    Move-Item -LiteralPath $cachePath -Destination $destination
    $plan.status = 'quarantined'
    $plan.applied = $true
    $plan | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
    try {
        $resolvedDestination = Resolve-SafeExisting $destination $true
        if (-not (Test-Within $resolvedDestination $caseDirectory) -or
            (Test-Within $resolvedDestination $releasePath) -or
            ((Get-BytecodeInventory $resolvedDestination | ConvertTo-Json -Compress) -cne
             ($cacheHashes | ConvertTo-Json -Compress))) {
            throw 'Quarantine destination does not preserve the exact cache names and bytes.'
        }
        $plan['quarantine_verified'] = $true
        $after = Read-Doctor
        $plan['doctor'] = $after
        Assert-Healthy $after
        if ((Get-Sha256 $InstallStatePath) -cne $stateHash) {
            throw 'Install state changed during recovery.'
        }
        $plan['self_test'] = Invoke-SelfTest
        $plan.status = 'verified'
        $plan | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
        $plan | ConvertTo-Json -Depth 20
    } catch {
        $plan['status'] = 'quarantined_validation_failed'
        $plan['error'] = $_.Exception.Message
        $plan | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $receiptPath -Encoding UTF8
        throw "Quarantine is preserved at $destination; validation failed: $($_.Exception.Message)"
    }
} finally {
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', $previousBytecodeSetting, 'Process')
}
