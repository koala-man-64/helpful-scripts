Set-StrictMode -Version Latest

function Get-ProfileRoots {
    [ordered]@{
        UserProfile = [Environment]::GetFolderPath('UserProfile')
        AppData = [Environment]::GetFolderPath('ApplicationData')
        LocalAppData = [Environment]::GetFolderPath('LocalApplicationData')
    }
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
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Text, [switch]$Json)
    $result = $Text
    foreach ($entry in (Get-ProfileRoots).GetEnumerator()) {
        if ($entry.Value) {
            $key = $entry.Key.ToUpperInvariant()
            if ($Json) {
                $escaped = $entry.Value.Replace('\', '\\')
                $result = $result -replace [regex]::Escape($escaped), "__${key}__"
            }
            $result = $result -replace [regex]::Escape($entry.Value), "__${key}__"
        }
    }
    $result
}

function ConvertFrom-PortableText {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Text, [switch]$Json)
    $result = $Text
    foreach ($entry in (Get-ProfileRoots).GetEnumerator()) {
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
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination)
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { throw "Missing source: $Source" }
    $text = ConvertTo-PortableText -Text (Get-Content -LiteralPath $Source -Raw) -Json:([IO.Path]::GetExtension($Source) -eq '.json')
    Test-SafeProfileContent -Path $Destination -Text $text
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Set-Content -LiteralPath $Destination -Value $text -Encoding utf8NoBOM -NoNewline
}

function Copy-PortableTree {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination)
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) { return }
    Get-ChildItem -LiteralPath $Source -Recurse -File | Where-Object {
        $_.Extension -ne '.pyc' -and $_.FullName -notmatch '[\\/]__pycache__[\\/]'
    } | ForEach-Object {
        $relative = $_.FullName.Substring($Source.Length).TrimStart([char[]]@('\', '/'))
        Copy-PortableFile -Source $_.FullName -Destination (Join-Path $Destination $relative)
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
    Get-ChildItem -LiteralPath $ProfileRoot -Recurse -File | ForEach-Object {
        if ($_.Extension -eq '.pyc' -or $_.FullName -match '[\\/]__pycache__[\\/]') { throw "Generated artifact present: $($_.FullName)" }
        $text = Get-Content -LiteralPath $_.FullName -Raw
        Test-SafeProfileContent -Path $_.FullName -Text $text
        if ($_.Extension -eq '.json') { $null = $text | ConvertFrom-Json }
    }
}

Export-ModuleMember -Function Get-ProfileRoots, ConvertTo-PortableText, ConvertFrom-PortableText, Test-RestrictedName, Test-SafeProfileContent, Copy-PortableFile, Copy-PortableTree, ConvertTo-SafeObject, Write-PortableJson, Test-AgenticProfileSnapshot
