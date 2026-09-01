[CmdletBinding()]
param(
    [string]$DestinationRoot = (Join-Path $PSScriptRoot '..\profile'),
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'ProfileTools.psm1') -Force

if (Test-Path -LiteralPath $DestinationRoot) {
    if (-not $Force) { throw "Destination exists. Use -Force to replace it: $DestinationRoot" }
    $expected = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\profile'))
    $actual = [IO.Path]::GetFullPath($DestinationRoot)
    if ($actual -ne $expected) { throw "Replacement is allowed only for this bundle's profile directory." }
    Remove-Item -LiteralPath $DestinationRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
$roots = Get-ProfileRoots
$codex = Join-Path $roots.UserProfile '.codex'
$claude = Join-Path $roots.UserProfile '.claude'
$codeUser = Join-Path $roots.AppData 'Code\User'

$configLines = Get-Content -LiteralPath (Join-Path $codex 'config.toml')
$portableLines = [Collections.Generic.List[string]]::new()
$include = $true
foreach ($line in $configLines) {
    if ($line -match '^\s*\[([^]]+)\]') { $include = $Matches[1] -eq 'features' }
    if ($include) { [void]$portableLines.Add($line) }
}
while ($portableLines.Count -gt 0 -and [string]::IsNullOrWhiteSpace($portableLines[$portableLines.Count - 1])) {
    $portableLines.RemoveAt($portableLines.Count - 1)
}
$configPath = Join-Path $DestinationRoot 'codex\config.template.toml'
$configText = ConvertTo-PortableText (($portableLines -join [Environment]::NewLine) + [Environment]::NewLine)
Test-SafeProfileContent -Path $configPath -Text $configText
New-Item -ItemType Directory -Path (Split-Path -Parent $configPath) -Force | Out-Null
Set-Content -LiteralPath $configPath -Value $configText -Encoding utf8NoBOM -NoNewline
foreach ($file in @('AGENTS.md', 'keybindings.json', 'hooks.json')) {
    Copy-PortableFile -Source (Join-Path $codex $file) -Destination (Join-Path $DestinationRoot "codex\\$file")
}
foreach ($skill in @('business-partner-agent', 'data-engineer-data-architect-advisor', 'git-hygiene-orchestrator', 'runtime-ownership-enforcer', 'strict-branch-and-merge-discipline')) {
    Copy-PortableTree -Source (Join-Path $codex "skills\\$skill") -Destination (Join-Path $DestinationRoot "codex\\skills\\$skill")
}
Copy-PortableTree -Source (Join-Path $codex 'rules') -Destination (Join-Path $DestinationRoot 'codex\rules')
$plugins = [regex]::Matches((Get-Content -LiteralPath (Join-Path $codex 'config.toml') -Raw), '(?m)^plugins\."([^"]+)"') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
Set-Content -LiteralPath (Join-Path $DestinationRoot 'codex\plugins.txt') -Value ($plugins -join [Environment]::NewLine) -Encoding utf8NoBOM

Copy-PortableFile -Source (Join-Path $claude 'CLAUDE.md') -Destination (Join-Path $DestinationRoot 'claude\CLAUDE.md')
$claudeSettings = Get-Content -LiteralPath (Join-Path $claude 'settings.json') -Raw | ConvertFrom-Json
$claudePortable = [ordered]@{}
foreach ($name in @('model', 'effortLevel', 'autoUpdatesChannel', 'theme', 'autoMode', 'skipWorkflowUsageWarning', 'hooks')) {
    if ($claudeSettings.PSObject.Properties[$name]) { $claudePortable[$name] = ConvertTo-SafeObject $claudeSettings.$name }
}
if ($claudeSettings.permissions) {
    $permissions = [ordered]@{}
    foreach ($property in $claudeSettings.permissions.PSObject.Properties) {
        if ($property.Name -ne 'additionalDirectories') { $permissions[$property.Name] = ConvertTo-SafeObject $property.Value $property.Name }
    }
    $claudePortable.permissions = $permissions
}
Write-PortableJson -Value $claudePortable -Destination (Join-Path $DestinationRoot 'claude\settings.template.json')
foreach ($directory in @('agents', 'skills', 'hooks')) {
    Copy-PortableTree -Source (Join-Path $claude $directory) -Destination (Join-Path $DestinationRoot "claude\\$directory")
}

$vscodeSettings = Get-Content -LiteralPath (Join-Path $codeUser 'settings.json') -Raw | ConvertFrom-Json
$vscodePortable = [ordered]@{}
foreach ($name in @('chatgpt.cliExecutable', 'chatgpt.localeOverride', 'chatgpt.openOnStartup', 'chatgpt.runCodexInWindowsSubsystemForLinux', 'claudeCode.preferredLocation', 'chat.useAgentSkills', 'chat.viewSessions.orientation', 'chat.hookFilesLocations', 'chat.mcp.assisted.nuget.enabled', 'chat.mcp.gallery.enabled')) {
    if ($vscodeSettings.PSObject.Properties[$name]) { $vscodePortable[$name] = ConvertTo-SafeObject $vscodeSettings.$name $name }
}
Write-PortableJson -Value $vscodePortable -Destination (Join-Path $DestinationRoot 'vscode\settings.json')
$mcp = Get-Content -LiteralPath (Join-Path $codeUser 'mcp.json') -Raw | ConvertFrom-Json
Write-PortableJson -Value (ConvertTo-SafeObject $mcp) -Destination (Join-Path $DestinationRoot 'vscode\mcp.template.json')
Copy-PortableTree -Source (Join-Path $codeUser 'prompts') -Destination (Join-Path $DestinationRoot 'vscode\prompts')
@('openai.chatgpt@26.825.51511', 'anthropic.claude-code@2.1.251', 'github.copilot', 'github.copilot-chat') |
    Set-Content -LiteralPath (Join-Path $DestinationRoot 'vscode\extensions.txt') -Encoding utf8NoBOM

Test-AgenticProfileSnapshot -ProfileRoot $DestinationRoot
Write-Host "Exported profile to $DestinationRoot"
