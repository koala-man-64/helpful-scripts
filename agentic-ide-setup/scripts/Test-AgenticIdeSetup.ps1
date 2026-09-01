[CmdletBinding()]
param([string]$ProfileRoot = (Join-Path $PSScriptRoot '..\profile'))

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'ProfileTools.psm1') -Force
Test-AgenticProfileSnapshot -ProfileRoot $ProfileRoot
$extensions = @(Get-Content -LiteralPath (Join-Path $ProfileRoot 'vscode\extensions.txt') | Where-Object { $_ })
if (@($extensions | Sort-Object -Unique).Count -ne $extensions.Count) { throw 'Extension manifest contains duplicates.' }
foreach ($relative in @('codex\AGENTS.md', 'codex\config.template.toml', 'codex\skills\agentcoord\SKILL.md', 'claude\CLAUDE.md', 'claude\settings.template.json', 'vscode\settings.json', 'vscode\mcp.template.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ProfileRoot $relative))) { throw "Missing profile artifact: $relative" }
}
$name = -join ([char[]](97,112,105,95,107,101,121))
$sample = "$name = 'abcdefghijklmnopqrstuvwxyz1234567890'"
try { Test-SafeProfileContent -Path 'sample.json' -Text $sample; throw 'Restricted-value detection did not reject the sample.' } catch {
    if ($_.Exception.Message -eq 'Restricted-value detection did not reject the sample.') { throw }
}
try { Test-SafeProfileContent -Path '.env' -Text 'x'; throw 'Excluded-name detection did not reject the sample.' } catch {
    if ($_.Exception.Message -eq 'Excluded-name detection did not reject the sample.') { throw }
}
Write-Host 'Agentic IDE setup validation passed.'
