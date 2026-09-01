[CmdletBinding()]
param(
    [string]$ProfilePath = (Join-Path $PSScriptRoot '..\config.example.json'),
    [string]$StateRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ClaudeFoundryHybrid')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'HybridTools.psm1') -Force

$resolvedProfile = (Resolve-Path -LiteralPath $ProfilePath).Path
$null = Invoke-CfhLauncher -Arguments @('validate', '--profile', $resolvedProfile)
$profile = Get-Content -LiteralPath $resolvedProfile -Raw | ConvertFrom-Json
$repoRoot = Get-CfhRepositoryRoot
$manifestPath = Join-Path $StateRoot 'install.json'
$manifest = if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
} else { $null }

$claudeCommand = Get-Command claude -ErrorAction SilentlyContinue
$codeCommand = Get-Command code -ErrorAction SilentlyContinue
$pythonVersion = $profile.gatewayLab.pythonVersion
$pythonAvailable = $false
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py "-$pythonVersion" -c 'import sys; print(sys.version.split()[0])' *> $null
    $pythonAvailable = $LASTEXITCODE -eq 0
}

$mcpServerName = if ($manifest) { $manifest.mcpServerName } else { 'foundry-model-consult' }
$mcpPython = if ($manifest) { $manifest.mcpPython } else {
    Join-Path $repoRoot 'mcp-chatbot\.venv\Scripts\python.exe'
}
$mcpConfig = Get-CfhMcpConfig $mcpPython
$mcpState = if ($claudeCommand) {
    Get-CfhMcpRegistrationState -Name $mcpServerName -ExpectedConfig $mcpConfig
} else { [pscustomobject]@{ Exists = $false; Matches = $false } }

$skillTarget = if ($manifest) { $manifest.skillTarget } else {
    Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude\skills\foundry-model-bench'
}
$skillExists = Test-Path -LiteralPath $skillTarget -PathType Container
$skillMatches = $false
if ($skillExists -and $manifest) {
    $skillMatches = (Get-CfhDirectoryHash $skillTarget) -eq $manifest.skillHash
}
$gatewayVenv = if ($manifest) { $manifest.gatewayVenv } else {
    Join-Path $repoRoot 'claude-foundry-hybrid\gateway\.venv'
}
$gatewayPython = Join-Path $gatewayVenv 'Scripts\python.exe'
$gatewayExecutable = Join-Path $gatewayVenv 'Scripts\litellm.exe'
$gatewayActualVersion = $null
$gatewayImportHealthy = $false
if (Test-Path -LiteralPath $gatewayPython -PathType Leaf) {
    $gatewayActualVersion = ((& $gatewayPython -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")') -join '').Trim()
    & $gatewayPython -c 'from litellm import run_server' *> $null
    $gatewayImportHealthy = $LASTEXITCODE -eq 0
}
$gatewayIndicator = if (Test-Path -LiteralPath $gatewayVenv -PathType Container) {
    @(Get-ChildItem -LiteralPath $gatewayVenv -Recurse -Filter 'litellm_init.pth' -File -ErrorAction SilentlyContinue).Count -gt 0
} else { $false }

$result = [ordered]@{
    profileValid = $true
    profilePath = $resolvedProfile
    installManifestPresent = [bool]$manifest
    claudeCliPresent = [bool]$claudeCommand
    claudeVersion = if ($claudeCommand) { ((& claude --version) -join ' ').Trim() } else { $null }
    vscodeCliPresent = [bool]$codeCommand
    gatewayPythonVersion = $pythonVersion
    gatewayPythonAvailable = $pythonAvailable -or ($gatewayActualVersion -eq $pythonVersion)
    gatewayActualPythonVersion = $gatewayActualVersion
    gatewayPythonMatchesProfile = $gatewayActualVersion -eq $pythonVersion
    mcpRuntimePresent = Test-Path -LiteralPath $mcpPython -PathType Leaf
    mcpRegistrationPresent = [bool]$mcpState.Exists
    mcpRegistrationMatches = [bool]$mcpState.Matches
    skillPresent = $skillExists
    skillMatchesManifest = $skillMatches
    gatewayInstalled = (Test-Path -LiteralPath $gatewayPython -PathType Leaf) -and (Test-Path -LiteralPath $gatewayExecutable -PathType Leaf)
    gatewayEntrypointImportHealthy = $gatewayImportHealthy
    gatewayCompromiseIndicatorPresent = $gatewayIndicator
    apiKeysPersistedByKit = $false
    liveRequestsMade = $false
    targetStatus = 'target-unverified'
}
$result | ConvertTo-Json -Depth 5
