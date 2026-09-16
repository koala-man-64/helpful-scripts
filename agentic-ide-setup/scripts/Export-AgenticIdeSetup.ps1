#Requires -Version 7.0
[CmdletBinding()]
param(
    [string]$DestinationRoot = (Join-Path $PSScriptRoot '..\profile'),
    [string]$SourceHome = [Environment]::GetFolderPath('UserProfile'),
    [switch]$Force
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'ProfileTools.psm1') -Force
$manifestPath = Join-Path $PSScriptRoot '..\setup-manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$destination = [IO.Path]::GetFullPath($DestinationRoot)
$expected = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\profile'))
foreach ($skill in $manifest.skills) {
    if ($skill.root -notin @('repo', 'home')) { throw "Unknown skill source root: $($skill.root)" }
    $root = if ($skill.root -eq 'repo') { $repo } else { $SourceHome }
    $null = Resolve-ProfileChild -Root $root -Relative $skill.source
    $null = Resolve-ProfileChild -Root $destination -Relative $skill.destination
}
if (Test-Path -LiteralPath $destination) {
    if (-not $Force) { throw "Destination exists. Use -Force to replace it: $destination" }
    if ($destination -ne $expected) { throw "Replacement is allowed only for this bundle's profile directory." }
}
Assert-NoReparsePath $destination
& python -B -c 'import sys, tomllib; assert sys.version_info >= (3, 11)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
$stage = "$destination.stage-$([guid]::NewGuid().ToString('N'))"
$backup = "$destination.backup-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $stage -Force | Out-Null
try {
    $codex = Join-Path $SourceHome '.codex'
    $claude = Join-Path $SourceHome '.claude'
    & python -B (Join-Path $PSScriptRoot 'codex_config.py') (Join-Path $codex 'config.toml') (Join-Path $stage 'codex') $manifestPath
    if ($LASTEXITCODE -ne 0) { throw 'Codex configuration export failed.' }
    foreach ($file in @('AGENTS.md', 'keybindings.json')) {
        Copy-PortableFile -Source (Join-Path $codex $file) -Destination (Join-Path $stage "codex/$file") -SourceHome $SourceHome
    }
    Copy-PortableFile -Source (Join-Path $claude 'CLAUDE.md') -Destination (Join-Path $stage 'claude/CLAUDE.md') -SourceHome $SourceHome
    foreach ($skill in $manifest.skills) {
        $root = if ($skill.root -eq 'repo') { $repo } else { $SourceHome }
        $source = Resolve-ProfileChild -Root $root -Relative $skill.source
        if (-not (Test-Path -LiteralPath (Join-Path $source 'SKILL.md'))) { throw "Missing required skill: $($skill.source)" }
        Copy-PortableTree -Source $source -Destination (Resolve-ProfileChild -Root $stage -Relative $skill.destination) -SourceHome $SourceHome
    }
    # Preserve reviewed repository-owned Claude resources; do not capture arbitrary
    # installed hooks, permission grants, MCP commands, or unrelated local skills.
    Copy-PortableTree -Source (Join-Path $expected 'claude/agents') -Destination (Join-Path $stage 'claude/agents') -SourceHome $SourceHome
    # Keep repository-owned hook source/tests for maintenance, never activation.
    Copy-PortableTree -Source (Join-Path $expected 'claude/hooks') -Destination (Join-Path $stage 'claude/hooks') -SourceHome $SourceHome
    Write-PortableJson -Value $manifest.claudeDefaults -Destination (Join-Path $stage 'claude/settings.template.json')
    Write-PortableJson -Value @{ 'chat.useAgentSkills' = $true; 'claudeCode.preferredLocation' = 'panel' } -Destination (Join-Path $stage 'vscode/settings.json')
    Write-PortableJson -Value @{ servers = @{} } -Destination (Join-Path $stage 'vscode/mcp.template.json')
    Set-Content -LiteralPath (Join-Path $stage 'vscode/extensions.txt') -Value ($manifest.extensions -join "`n") -Encoding utf8NoBOM
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $stage 'setup-manifest.json')
    & (Join-Path $PSScriptRoot 'Test-AgenticIdeSetup.ps1') -ProfileRoot $stage
    # Same-volume rename preserves the previous valid snapshot until validation succeeds.
    if (Test-Path -LiteralPath $destination) { Move-Item -LiteralPath $destination -Destination $backup }
    try { Move-Item -LiteralPath $stage -Destination $destination }
    catch {
        if (Test-Path -LiteralPath $backup) { Move-Item -LiteralPath $backup -Destination $destination }
        throw
    }
    if (Test-Path -LiteralPath $backup) { Write-Host "Previous profile retained: $backup" }
    Write-Host "Exported validated profile to $destination"
} catch {
    Write-Warning "Export failed; prior profile retained. Diagnostic staging directory: $stage"
    throw
}
