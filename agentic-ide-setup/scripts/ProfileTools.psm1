#Requires -Version 7.0
Set-StrictMode -Version Latest

function Get-ProfileRoots {
    param([string]$HomeRoot = [Environment]::GetFolderPath('UserProfile'))
    [ordered]@{
        LocalAppData = Join-Path $HomeRoot 'AppData/Local'
        AppData = Join-Path $HomeRoot 'AppData/Roaming'
        UserProfile = $HomeRoot
    }
}

function Assert-NoReparsePath {
    param([Parameter(Mandatory)][string]$Path)
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse point is not an allowed profile path: $cursor"
            }
        }
        $cursor = Split-Path -Parent $cursor
    }
}

function Resolve-ProfileChild {
    param([Parameter(Mandatory)][string]$Root, [Parameter(Mandatory)][string]$Relative)
    if ([IO.Path]::IsPathRooted($Relative) -or $Relative -match '(^|[\\/])\.\.([\\/]|$)' -or $Relative.Contains(':')) {
        throw "Unsafe relative profile path: $Relative"
    }
    $prefix = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $child = [IO.Path]::GetFullPath((Join-Path $Root $Relative))
    if (-not $child.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw "Profile path escapes root: $Relative" }
    return $child
}

function Get-RestrictedTerms {
    $codes = @(
        @(97,112,105,95,107,101,121), @(116,111,107,101,110), @(115,101,99,114,101,116),
        @(112,97,115,115,119,111,114,100), @(99,114,101,100,101,110,116,105,97,108),
        @(97,117,116,104,111,114,105,122,97,116,105,111,110)
    )
    foreach ($term in $codes) { -join ($term | ForEach-Object { [char]$_ }) }
}

function Get-ExcludedArtifactNames {
    $codes = @(
        @(46,101,110,118), @(97,117,116,104,46,106,115,111,110),
        @(99,114,101,100,101,110,116,105,97,108,115,46,106,115,111,110)
    )
    foreach ($name in $codes) { -join ($name | ForEach-Object { [char]$_ }) }
}

function ConvertTo-PortableText {
    # JSON string values carry doubled separators, so a path inside settings.json
    # never matches the raw profile root. Without -Json a hook command path is
    # exported verbatim and pins the profile to this machine.
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Text, [switch]$Json,
        [string]$SourceHome = [Environment]::GetFolderPath('UserProfile'))
    $result = $Text
    foreach ($entry in (Get-ProfileRoots -HomeRoot $SourceHome).GetEnumerator()) {
        if ($entry.Value) {
            $key = $entry.Key.ToUpperInvariant()
            foreach ($form in @($entry.Value.Replace('\', '\\'), $entry.Value, $entry.Value.Replace('\', '/'))) {
                $result = $result -replace [regex]::Escape($form), "__${key}__"
            }
        }
    }
    $result
}

function ConvertFrom-PortableText {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Text, [switch]$Json,
        [string]$DestinationRoot = [Environment]::GetFolderPath('UserProfile'))
    $result = $Text
    foreach ($entry in (Get-ProfileRoots -HomeRoot $DestinationRoot).GetEnumerator()) {
        $key = $entry.Key.ToUpperInvariant()
        $value = if ($Json) { $entry.Value.Replace('\', '\\') } else { $entry.Value }
        $result = $result.Replace("__${key}__", $value)
    }
    $result
}

function Test-RestrictedName {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Name)
    if ((Get-ExcludedArtifactNames) -contains $Name.ToLowerInvariant()) { return $true }
    foreach ($term in Get-RestrictedTerms) { if ($Name -match "(?i)(^|[._-])$([regex]::Escape($term))([._-]|$)") { return $true } }
    $false
}

function Test-SafeProfileContent {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][AllowEmptyString()][string]$Text)
    if (Test-RestrictedName ([IO.Path]::GetFileName($Path))) { throw "Excluded artifact: $Path" }
    $terms = (Get-RestrictedTerms | ForEach-Object { [regex]::Escape($_) }) -join '|'
    $pattern = '(?im)^\s*[''\"]?[A-Za-z0-9_.-]*(?:' + $terms + ')[A-Za-z0-9_.-]*[''\"]?\s*[:=]\s*[''\"][A-Za-z0-9_./+=-]{20,}[''\"]'
    if ($Text -match $pattern) { throw "Restricted value found in: $Path" }
}

function Copy-PortableFile {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination,
        [string]$SourceHome = [Environment]::GetFolderPath('UserProfile'))
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { throw "Missing source: $Source" }
    Assert-NoReparsePath $Source
    $text = ConvertTo-PortableText -Text (Get-Content -LiteralPath $Source -Raw) -SourceHome $SourceHome -Json:([IO.Path]::GetExtension($Source) -eq '.json')
    Test-SafeProfileContent -Path $Destination -Text $text
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Set-Content -LiteralPath $Destination -Value $text -Encoding utf8NoBOM -NoNewline
}

function Copy-PortableTree {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination,
        [string]$SourceHome = [Environment]::GetFolderPath('UserProfile'))
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) { return }
    Assert-NoReparsePath $Source
    if (Get-ChildItem -LiteralPath $Source -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) { throw "Reparse point in source tree: $Source" }
    Get-ChildItem -LiteralPath $Source -Recurse -File | Where-Object {
        $_.Extension -ne '.pyc' -and $_.FullName -notmatch '[\\/]__pycache__[\\/]'
    } | ForEach-Object {
        $relative = $_.FullName.Substring($Source.Length).TrimStart([char[]]@('\', '/'))
        Copy-PortableFile -Source $_.FullName -Destination (Join-Path $Destination $relative) -SourceHome $SourceHome
    }
}

function ConvertTo-SafeObject {
    param($Value, [string]$PropertyName = '')
    if ($PropertyName -eq 'env') { return '__REVIEW_REQUIRED__' }
    if (Test-RestrictedName $PropertyName) { return '__REVIEW_REQUIRED__' }
    if ($null -eq $Value -or $Value -is [string] -or $Value -is [ValueType]) { return $Value }
    if ($Value -is [Collections.IEnumerable]) {
        # Build the list explicitly and return it comma-wrapped. Emitting an array
        # through the pipeline unrolls it, so a one-element array (every hooks
        # entry) collapsed into a bare object and the exported settings no longer
        # matched the shape Claude Code reads.
        $items = [Collections.Generic.List[object]]::new()
        foreach ($item in $Value) { [void]$items.Add((ConvertTo-SafeObject $item)) }
        return , $items.ToArray()
    }
    $result = [ordered]@{}
    foreach ($property in $Value.PSObject.Properties) { $result[$property.Name] = ConvertTo-SafeObject $property.Value $property.Name }
    $result
}

function Write-PortableJson {
    param([Parameter(Mandatory)]$Value, [Parameter(Mandatory)][string]$Destination)
    $text = ConvertTo-PortableText -Text (($Value | ConvertTo-Json -Depth 48) + [Environment]::NewLine) -Json
    Test-SafeProfileContent -Path $Destination -Text $text
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Set-Content -LiteralPath $Destination -Value $text -Encoding utf8NoBOM -NoNewline
}

function Test-AgenticProfileSnapshot {
    param([Parameter(Mandatory)][string]$ProfileRoot)
    if (-not (Test-Path -LiteralPath $ProfileRoot -PathType Container)) { throw "Profile directory does not exist: $ProfileRoot" }
    Assert-NoReparsePath $ProfileRoot
    if (Get-ChildItem -LiteralPath $ProfileRoot -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) { throw 'Reparse point in profile.' }
    Get-ChildItem -LiteralPath $ProfileRoot -Recurse -File | ForEach-Object {
        if ($_.Extension -eq '.pyc' -or $_.FullName -match '[\\/]__pycache__[\\/]') { throw "Generated artifact present: $($_.FullName)" }
        $text = Get-Content -LiteralPath $_.FullName -Raw
        Test-SafeProfileContent -Path $_.FullName -Text $text
        if ($_.Extension -eq '.json') { $null = $text | ConvertFrom-Json }
        if ($_.Name -in @('hooks.json', 'config.template.toml', 'settings.template.json', 'mcp.template.json')) {
            if ($text -match '(?i)[A-Z]:[\\/]|__REVIEW_REQUIRED__|CodexWorkflowHooks|cua_node') { throw "Nonportable executable configuration: $($_.FullName)" }
            if ($_.Name -eq 'hooks.json') { throw 'Managed hooks must be installed separately.' }
        }
        $unknown = [regex]::Matches($text, '__[A-Z][A-Z_]+__') | Where-Object { $_.Value -notin @('__USERPROFILE__', '__APPDATA__', '__LOCALAPPDATA__') }
        if ($unknown -and $_.Extension -in @('.json', '.toml')) { throw "Unresolved marker in $($_.FullName)" }
    }
}

Export-ModuleMember -Function Resolve-ProfileChild, Assert-NoReparsePath, Get-ProfileRoots, ConvertTo-PortableText, ConvertFrom-PortableText, Test-RestrictedName, Test-SafeProfileContent, Copy-PortableFile, Copy-PortableTree, ConvertTo-SafeObject, Write-PortableJson, Test-AgenticProfileSnapshot
