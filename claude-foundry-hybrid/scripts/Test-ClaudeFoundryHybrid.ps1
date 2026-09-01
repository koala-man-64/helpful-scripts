[CmdletBinding()]
param([string]$ProfilePath = (Join-Path $PSScriptRoot '..\config.example.json'))

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'HybridTools.psm1') -Force

$resolvedProfile = (Resolve-Path -LiteralPath $ProfilePath).Path
$repoRoot = Get-CfhRepositoryRoot
$packageRoot = Join-Path $repoRoot 'claude-foundry-hybrid'

$tokens = $null
foreach ($script in Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object Extension -in @('.ps1', '.psm1')) {
    $parseErrors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile($script.FullName, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw "PowerShell parse failure in $($script.Name): $($parseErrors[0].Message)" }
}

Invoke-CfhLauncher -Arguments @('validate', '--profile', $resolvedProfile)
$runtime = Get-CfhPythonLauncher
foreach ($mode in @('Native', 'Hybrid', 'GatewayLab')) {
    $output = & $runtime.Command @($runtime.Prefix) (Join-Path $PSScriptRoot 'launcher.py') 'describe-env' '--profile' $resolvedProfile '--mode' $mode '--surface' 'Cli'
    if ($LASTEXITCODE -ne 0) { throw "Environment description failed for $mode." }
    if (($output -join "`n") -match 'description-only-(?:native|mcp)-key|description-only-token') {
        throw "Environment description leaked a sentinel value for $mode."
    }
}

$lockPath = Join-Path $packageRoot 'gateway\requirements.lock'
if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) { throw "Missing gateway lock: $lockPath" }
$lockText = Get-Content -LiteralPath $lockPath -Raw
if ($lockText -notmatch 'litellm.*==1\.99\.0' -or $lockText -notmatch '--hash=sha256:') {
    throw 'Gateway lock is not pinned and hash-locked to LiteLLM 1.99.0.'
}
if ($lockText -match 'litellm.*==1\.82\.(7|8)') { throw 'Gateway lock contains a compromised LiteLLM release.' }

& $runtime.Command @($runtime.Prefix) -m pytest (Join-Path $packageRoot 'tests')
if ($LASTEXITCODE -ne 0) { throw 'Claude Foundry hybrid Python tests failed.' }
$gatewayExecutable = Join-Path $packageRoot 'gateway\.venv\Scripts\litellm.exe'
if (Test-Path -LiteralPath $gatewayExecutable -PathType Leaf) {
    Invoke-CfhLauncher -Arguments @(
        'smoke-gateway', '--profile', $resolvedProfile,
        '--working-directory', $repoRoot,
        '--gateway-executable', $gatewayExecutable
    )
}
Write-Host 'Claude Foundry hybrid offline validation passed.'
