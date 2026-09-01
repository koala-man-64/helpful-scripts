[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ProfilePath,
    [ValidateSet('Native', 'Hybrid', 'GatewayLab')][string]$Mode = 'Hybrid',
    [ValidateSet('Cli', 'VSCode')][string]$Surface = 'Cli',
    [string]$WorkingDirectory = (Get-Location).Path,
    [string]$ClaudeExecutable = 'claude',
    [string]$VSCodeExecutable = 'code',
    [switch]$ReuseNativeKeyForMcp,
    [switch]$EnableAgentTools,
    [switch]$DescribeEnvironment,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ClaudeArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'HybridTools.psm1') -Force

$resolvedProfile = (Resolve-Path -LiteralPath $ProfilePath).Path
$resolvedWorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path
$repoRoot = Get-CfhRepositoryRoot
$gatewayExecutable = Join-Path $repoRoot 'claude-foundry-hybrid\gateway\.venv\Scripts\litellm.exe'

if ($Mode -eq 'GatewayLab' -and $Surface -ne 'Cli') {
    throw 'GatewayLab is CLI-only until its target-host compatibility gates pass.'
}
if ($DescribeEnvironment) {
    Invoke-CfhLauncher -Arguments @('describe-env', '--profile', $resolvedProfile, '--mode', $Mode, '--surface', $Surface)
    return
}
if ($Surface -eq 'VSCode' -and (Get-Process -Name Code -ErrorAction SilentlyContinue)) {
    throw 'Close every running VS Code window first so the new process inherits the Foundry environment.'
}

$secretNames = @('CFH_NATIVE_API_KEY', 'CFH_MCP_API_KEY', 'CFH_GATEWAY_API_KEY')
$saved = @{}
foreach ($name in $secretNames) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    if ($Mode -in @('Native', 'Hybrid')) {
        $nativeKey = Get-CfhSecretValue -EnvironmentName 'CFH_NATIVE_API_KEY' -Prompt 'Foundry API key for native Claude'
        [Environment]::SetEnvironmentVariable('CFH_NATIVE_API_KEY', $nativeKey, 'Process')
        if ($Mode -eq 'Hybrid') {
            $mcpKey = if ($ReuseNativeKeyForMcp) { $nativeKey } else {
                Get-CfhSecretValue -EnvironmentName 'CFH_MCP_API_KEY' -Prompt 'Foundry API key for MCP non-Claude models'
            }
            [Environment]::SetEnvironmentVariable('CFH_MCP_API_KEY', $mcpKey, 'Process')
        }
    } else {
        $gatewayKey = Get-CfhSecretValue -EnvironmentName 'CFH_GATEWAY_API_KEY' -Prompt 'Foundry API key for the isolated gateway lab'
        [Environment]::SetEnvironmentVariable('CFH_GATEWAY_API_KEY', $gatewayKey, 'Process')
    }

    $arguments = @(
        'launch', '--profile', $resolvedProfile, '--mode', $Mode, '--surface', $Surface,
        '--working-directory', $resolvedWorkingDirectory,
        '--claude-executable', $ClaudeExecutable,
        '--vscode-executable', $VSCodeExecutable,
        '--gateway-executable', $gatewayExecutable
    )
    if ($EnableAgentTools) { $arguments += '--enable-agent-tools' }
    if ($ClaudeArgs) { $arguments += '--'; $arguments += $ClaudeArgs }
    Invoke-CfhLauncher -Arguments $arguments
} finally {
    foreach ($name in $secretNames) {
        [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
    }
    $nativeKey = $null
    $mcpKey = $null
    $gatewayKey = $null
}
