[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('Codex', 'Claude', 'VSCode')][string[]]$Components = @('Codex', 'Claude', 'VSCode'),
    [string]$ProfileRoot = (Join-Path $PSScriptRoot '..\profile'),
    [string]$DestinationRoot = [Environment]::GetFolderPath('UserProfile'),
    [switch]$Apply,
    [switch]$Overwrite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'ProfileTools.psm1') -Force
Test-AgenticProfileSnapshot -ProfileRoot $ProfileRoot

function Get-TargetRelativePath {
    param([Parameter(Mandatory)][string]$Relative)
    switch ($Relative) {
        'config.template.toml' { return 'config.toml' }
        'settings.template.json' { return 'settings.json' }
        'mcp.template.json' { return 'mcp.json' }
        default { return $Relative }
    }
}

function Install-Tree {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination)
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) { return }
    $sourceRoot = (Resolve-Path -LiteralPath $Source).Path.TrimEnd([char[]]@('\', '/'))
    Get-ChildItem -LiteralPath $sourceRoot -Recurse -File | ForEach-Object {
        if ($_.Name -in @('extensions.txt', 'plugins.txt')) { return }
        $relative = $_.FullName.Substring($sourceRoot.Length).TrimStart([char[]]@('\', '/'))
        $target = Join-Path $Destination (Get-TargetRelativePath $relative)
        if ((Test-Path -LiteralPath $target -PathType Leaf) -and -not $Overwrite) {
            Write-Warning "Skipped existing file: $target (use -Overwrite to replace it)"
            return
        }
        if (-not $Apply) { Write-Host "Would install $target"; return }
        if ($PSCmdlet.ShouldProcess($target, 'Install profile artifact')) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            if (Test-Path -LiteralPath $target) {
                Copy-Item -LiteralPath $target -Destination "$target.agentic-ide-setup-backup-$(Get-Date -Format yyyyMMddHHmmss)" -Force
            }
            $text = ConvertFrom-PortableText (Get-Content -LiteralPath $_.FullName -Raw)
            Set-Content -LiteralPath $target -Value $text -Encoding utf8NoBOM -NoNewline
        }
    }
}

if ('Codex' -in $Components) { Install-Tree (Join-Path $ProfileRoot 'codex') (Join-Path $DestinationRoot '.codex') }
if ('Claude' -in $Components) { Install-Tree (Join-Path $ProfileRoot 'claude') (Join-Path $DestinationRoot '.claude') }
if ('VSCode' -in $Components) {
    $codeUser = Join-Path $DestinationRoot 'AppData\Roaming\Code\User'
    Install-Tree (Join-Path $ProfileRoot 'vscode') $codeUser
    $code = Get-Command code -ErrorAction SilentlyContinue
    if ($code) {
        Get-Content -LiteralPath (Join-Path $ProfileRoot 'vscode\extensions.txt') | Where-Object { $_ } | ForEach-Object {
            if (-not $Apply) { Write-Host "Would install VS Code extension $_"; return }
            if ($PSCmdlet.ShouldProcess($_, 'Install VS Code extension')) { & $code.Source --install-extension $_ }
        }
    } else { Write-Warning 'VS Code command not found; use the extension manifest after installing VS Code.' }
}
