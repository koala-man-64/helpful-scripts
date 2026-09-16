#Requires -Version 7.0
[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('Codex', 'Claude', 'VSCode')][string[]]$Components = @('Codex', 'Claude', 'VSCode'),
    [string]$ProfileRoot = (Join-Path $PSScriptRoot '..\profile'),
    [string]$DestinationRoot = [Environment]::GetFolderPath('UserProfile'),
    [switch]$Apply,
    [switch]$Overwrite,
    [switch]$InstallExtensions
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'ProfileTools.psm1') -Force
& (Join-Path $PSScriptRoot 'Test-AgenticIdeSetup.ps1') -ProfileRoot $ProfileRoot
$DestinationRoot = [IO.Path]::GetFullPath($DestinationRoot)
Assert-NoReparsePath $DestinationRoot
$isActiveHome = $DestinationRoot.TrimEnd('\', '/') -eq [Environment]::GetFolderPath('UserProfile').TrimEnd('\', '/')
if ($InstallExtensions -and -not $isActiveHome) { throw 'Extension installation is allowed only for the active home; staged installs are files-only.' }
function Get-TargetRelativePath {
    param([Parameter(Mandatory)][string]$Relative)
    switch ($Relative) {
        'config.template.toml' { return 'config.toml' }
        'settings.template.json' { return 'settings.json' }
        'mcp.template.json' { return 'mcp.json' }
        default { return $Relative }
    }
}


# Check every existing destination ancestor before the first write.
foreach ($component in $Components) {
    $relative = switch ($component) { Codex { '.codex' }; Claude { '.claude' }; VSCode { 'AppData/Roaming/Code/User' } }
    $targetRoot = Join-Path $DestinationRoot $relative
    Assert-NoReparsePath $targetRoot
    $sourceRoot = (Resolve-Path -LiteralPath (Join-Path $ProfileRoot $component.ToLowerInvariant())).Path
    Get-ChildItem -LiteralPath $sourceRoot -Recurse -File | ForEach-Object {
        $relativeFile = Get-TargetRelativePath ($_.FullName.Substring($sourceRoot.Length).TrimStart('\', '/'))
        Assert-NoReparsePath (Join-Path $targetRoot $relativeFile)
    }
}


function Install-Tree {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination)
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) { return }
    $sourceRoot = (Resolve-Path -LiteralPath $Source).Path.TrimEnd([char[]]@('\', '/'))
    Get-ChildItem -LiteralPath $sourceRoot -Recurse -File | ForEach-Object {
        if ($_.Name -in @('extensions.txt', 'plugins.txt')) { return }
        $relative = $_.FullName.Substring($sourceRoot.Length).TrimStart([char[]]@('\', '/'))
        if ($relative -match '^hooks[\\/]') { return } # Repository source, owned by the hook installer.
        $target = Join-Path $Destination (Get-TargetRelativePath $relative)
        if ((Test-Path -LiteralPath $target -PathType Leaf) -and $_.Name -in @('config.template.toml', 'settings.template.json', 'mcp.template.json')) {
            Write-Warning "Preserved host-owned configuration: $target. Merge reviewed settings manually; hooks and trust remain host-owned."
            return
        }
        if ((Test-Path -LiteralPath $target -PathType Leaf) -and -not $Overwrite) {
            Write-Warning "Skipped existing file: $target (use -Overwrite to replace it)"
            return
        }
        if (-not $Apply) { Write-Host "Would install $target"; return }
        if ($PSCmdlet.ShouldProcess($target, 'Install profile artifact')) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            if (Test-Path -LiteralPath $target) {
                Copy-Item -LiteralPath $target -Destination "$target.agentic-ide-setup-backup-$([guid]::NewGuid().ToString('N'))"
            }
            $text = ConvertFrom-PortableText -Text (Get-Content -LiteralPath $_.FullName -Raw) -DestinationRoot $DestinationRoot -Json:($_.Extension -in @('.json', '.toml'))
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
    if ($InstallExtensions -and $code) {
        Get-Content -LiteralPath (Join-Path $ProfileRoot 'vscode\extensions.txt') | Where-Object { $_ } | ForEach-Object {
            if (-not $Apply) { Write-Host "Would install VS Code extension $_"; return }
            if ($PSCmdlet.ShouldProcess($_, 'Install VS Code extension')) {
                & $code.Source --install-extension $_
                if ($LASTEXITCODE -ne 0) { throw "VS Code extension installation failed: $_" }
            }
        }
    } elseif ($InstallExtensions) { throw 'VS Code command not found.' }
}
$manifest = Get-Content -LiteralPath (Join-Path $ProfileRoot 'setup-manifest.json') -Raw | ConvertFrom-Json
Write-Host 'Files-only profile operation complete. External dependencies are not installed or certified:'
$manifest.dependencies | ForEach-Object { Write-Host "- $_" }
