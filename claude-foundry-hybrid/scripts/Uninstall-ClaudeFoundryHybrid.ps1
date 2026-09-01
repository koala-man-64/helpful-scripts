[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Apply,
    [string]$SkillRoot = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude\skills'),
    [string]$StateRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ClaudeFoundryHybrid')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'HybridTools.psm1') -Force

$manifestPath = Join-Path $StateRoot 'install.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Ownership manifest not found: $manifestPath. Nothing will be removed by guesswork."
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.version -ne 1) { throw "Unsupported ownership manifest version: $($manifest.version)" }
$repoRoot = Get-CfhRepositoryRoot
$expectedSkillRoot = [IO.Path]::GetFullPath($SkillRoot)
$mcpVenv = Split-Path -Parent (Split-Path -Parent $manifest.mcpPython)
$mcpOwnerMarker = Join-Path $mcpVenv '.claude-foundry-hybrid-owner.json'
$gatewayOwnerMarker = Join-Path $manifest.gatewayVenv '.claude-foundry-hybrid-owner.json'

if ([IO.Path]::GetFullPath($manifest.skillRoot) -ne $expectedSkillRoot) {
    throw 'The supplied -SkillRoot does not match the root recorded by the installer.'
}
if (-not (Test-CfhPathWithin -Path $manifest.skillTarget -Root $expectedSkillRoot) -or
    [IO.Path]::GetFileName($manifest.skillTarget) -ne 'foundry-model-bench') {
    throw "Manifest contains an unsafe skill target: $($manifest.skillTarget)"
}
if (-not (Test-CfhPathWithin -Path $manifest.mcpPython -Root $repoRoot) -or
    -not $manifest.mcpPython.EndsWith('.venv\Scripts\python.exe', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Manifest contains an unsafe MCP runtime path: $($manifest.mcpPython)"
}
if (-not (Test-CfhPathWithin -Path $manifest.gatewayVenv -Root $repoRoot) -or
    [IO.Path]::GetFileName($manifest.gatewayVenv) -ne '.venv') {
    throw "Manifest contains an unsafe gateway runtime path: $($manifest.gatewayVenv)"
}
if (-not (Test-CfhPathWithin -Path $manifest.gatewaySbom -Root $StateRoot)) {
    throw "Manifest contains an unsafe gateway inventory path: $($manifest.gatewaySbom)"
}
if ($manifest.mcpVenvCreated -and (Test-Path -LiteralPath $mcpVenv -PathType Container)) {
    $expectedMarker = Get-CfhVenvOwnerText -StateRoot $StateRoot -Kind mcp
    if (-not (Test-Path -LiteralPath $mcpOwnerMarker -PathType Leaf) -or
        (Get-Content -LiteralPath $mcpOwnerMarker -Raw) -ne $expectedMarker) {
        throw 'Owned MCP venv marker is missing or changed; refusing recursive removal.'
    }
}
if ($manifest.gatewayVenvCreated -and (Test-Path -LiteralPath $manifest.gatewayVenv -PathType Container)) {
    $expectedMarker = Get-CfhVenvOwnerText -StateRoot $StateRoot -Kind gateway
    if (-not (Test-Path -LiteralPath $gatewayOwnerMarker -PathType Leaf) -or
        (Get-Content -LiteralPath $gatewayOwnerMarker -Raw) -ne $expectedMarker) {
        throw 'Owned gateway venv marker is missing or changed; refusing recursive removal.'
    }
}

$expectedMcpConfig = Get-CfhMcpConfig $manifest.mcpPython
$expectedMcpJson = $expectedMcpConfig | ConvertTo-Json -Depth 8 -Compress
if ((Get-CfhTextHash $expectedMcpJson) -ne $manifest.mcpConfigHash) {
    throw 'This script version cannot reproduce the owned MCP registration. Use the original kit version or remove it manually.'
}
$mcpState = Get-CfhMcpRegistrationState -Name $manifest.mcpServerName -ExpectedConfig $expectedMcpConfig
if ($mcpState.Exists -and -not $mcpState.Matches) {
    throw "MCP server '$($manifest.mcpServerName)' changed since installation; refusing to remove it."
}

if (Test-Path -LiteralPath $manifest.skillTarget -PathType Container) {
    $currentHash = Get-CfhDirectoryHash $manifest.skillTarget
    if ($currentHash -ne $manifest.skillHash) {
        throw "Installed skill changed since installation; refusing to move it: $($manifest.skillTarget)"
    }
}
$skillBackup = if ($manifest.PSObject.Properties.Name -contains 'skillBackup') { $manifest.skillBackup } else { $null }
$skillBackupHash = if ($manifest.PSObject.Properties.Name -contains 'skillBackupHash') { $manifest.skillBackupHash } else { $null }
if ($skillBackup) {
    if (-not (Test-CfhPathWithin -Path $skillBackup -Root $StateRoot) -or
        -not (Test-Path -LiteralPath $skillBackup -PathType Container)) {
        throw "Recorded skill backup is missing or unsafe: $skillBackup"
    }
    if ((Get-CfhDirectoryHash $skillBackup) -ne $skillBackupHash) {
        throw "Recorded skill backup changed; refusing rollback: $skillBackup"
    }
}

Write-Host "Would remove MCP registration: $($manifest.mcpServerName)"
Write-Host "Would move installed skill out of: $($manifest.skillTarget)"
if ($manifest.mcpVenvCreated) { Write-Host "Would remove owned MCP venv: $mcpVenv" }
if ($manifest.gatewayVenvCreated) { Write-Host "Would remove owned gateway venv: $($manifest.gatewayVenv)" }
if ($skillBackup) { Write-Host "Would restore previous skill from: $skillBackup" }
if (-not $Apply) { Write-Host 'Dry run only. Re-run with -Apply to perform rollback.'; return }

if ($mcpState.Exists -and $PSCmdlet.ShouldProcess($manifest.mcpServerName, 'Remove exact user-scope Claude MCP registration')) {
    & claude mcp remove --scope user $manifest.mcpServerName
    if ($LASTEXITCODE -ne 0) { throw "Claude MCP removal failed with code $LASTEXITCODE." }
}

if (Test-Path -LiteralPath $manifest.skillTarget -PathType Container) {
    $trash = Join-Path $StateRoot ("trash\foundry-model-bench-" + (Get-Date -Format yyyyMMddHHmmss))
    if ($PSCmdlet.ShouldProcess($manifest.skillTarget, "Move installed skill to $trash")) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $trash) -Force | Out-Null
        Move-Item -LiteralPath $manifest.skillTarget -Destination $trash
    }
}
if ($skillBackup -and $PSCmdlet.ShouldProcess($skillBackup, "Restore previous skill to $($manifest.skillTarget)")) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $manifest.skillTarget) -Force | Out-Null
    Move-Item -LiteralPath $skillBackup -Destination $manifest.skillTarget
}

if ($manifest.gatewayVenvCreated -and (Test-Path -LiteralPath $manifest.gatewayVenv -PathType Container)) {
    if ($PSCmdlet.ShouldProcess($manifest.gatewayVenv, 'Remove regenerable owned gateway venv')) {
        Remove-Item -LiteralPath $manifest.gatewayVenv -Recurse -Force
    }
}
if ($manifest.mcpVenvCreated -and (Test-Path -LiteralPath $mcpVenv -PathType Container)) {
    if ([IO.Path]::GetFileName($mcpVenv) -ne '.venv') { throw 'MCP venv target is not named .venv.' }
    if ($PSCmdlet.ShouldProcess($mcpVenv, 'Remove regenerable owned MCP venv')) {
        Remove-Item -LiteralPath $mcpVenv -Recurse -Force
    }
}
if (Test-Path -LiteralPath $manifest.gatewaySbom -PathType Leaf) {
    $sbomArchive = Join-Path $StateRoot ("trash\gateway-sbom-" + (Get-Date -Format yyyyMMddHHmmss) + '.txt')
    if ($PSCmdlet.ShouldProcess($manifest.gatewaySbom, "Archive gateway inventory to $sbomArchive")) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $sbomArchive) -Force | Out-Null
        Move-Item -LiteralPath $manifest.gatewaySbom -Destination $sbomArchive
    }
}

$manifestArchive = Join-Path $StateRoot ("trash\install-" + (Get-Date -Format yyyyMMddHHmmss) + '.json')
if ($PSCmdlet.ShouldProcess($manifestPath, "Archive ownership manifest to $manifestArchive")) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $manifestArchive) -Force | Out-Null
    Move-Item -LiteralPath $manifestPath -Destination $manifestArchive
}
Write-Host 'Claude Foundry hybrid kit rollback completed. API keys were never stored by the kit.'
