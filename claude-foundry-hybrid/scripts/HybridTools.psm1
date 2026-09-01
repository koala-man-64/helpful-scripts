Set-StrictMode -Version Latest

function Get-CfhRepositoryRoot {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}

function Write-CfhUtf8NoBom {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Text
    )
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

function Get-CfhDirectoryHash {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Directory not found: $Path"
    }
    $root = (Resolve-Path -LiteralPath $Path).Path.TrimEnd([char[]]@('\', '/'))
    $lines = Get-ChildItem -LiteralPath $root -Recurse -File | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($root.Length).TrimStart([char[]]@('\', '/')).Replace('\', '/')
        "$relative|$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant())"
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
    $sha = [Security.Cryptography.SHA256]::Create()
    try { ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-CfhTextHash {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Text)
    $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Test-CfhPathWithin {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Root
    )
    $fullPath = [IO.Path]::GetFullPath($Path).TrimEnd([char[]]@('\', '/'))
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd([char[]]@('\', '/'))
    $prefix = $fullRoot + [IO.Path]::DirectorySeparatorChar
    $fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Get-CfhMcpConfig {
    param([Parameter(Mandatory)][string]$PythonPath)
    [ordered]@{
        type = 'stdio'
        command = [IO.Path]::GetFullPath($PythonPath)
        args = @('-m', 'mcp_chatbot.bench')
        env = [ordered]@{
            FOUNDRY_OPENAI_BASE_URL = '${CFH_FOUNDRY_OPENAI_BASE_URL}'
            FOUNDRY_API_KEY = '${CFH_MCP_API_KEY}'
            FOUNDRY_DEFAULT_DEPLOYMENT = '${CFH_DEFAULT_DEPLOYMENT}'
            FOUNDRY_ALLOWED_DEPLOYMENTS_JSON = '${CFH_ALLOWED_DEPLOYMENTS_JSON}'
            FOUNDRY_TIMEOUT_SECONDS = '${CFH_FOUNDRY_TIMEOUT_SECONDS:-120}'
        }
    }
}

function Get-CfhMcpRegistrationState {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)]$ExpectedConfig
    )

    $details = @(& claude mcp get $Name 2>&1)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        return [pscustomobject]@{ Exists = $false; Matches = $false }
    }

    # Claude Code currently exposes no structured output for `mcp get`. Match
    # every nonsecret field we own and refuse mutation if any field drifted.
    $text = $details -join "`n"
    $requiredLines = @(
        "Type: $($ExpectedConfig.type)",
        "Command: $($ExpectedConfig.command)",
        "Args: $($ExpectedConfig.args -join ' ')"
    )
    foreach ($entry in $ExpectedConfig.env.GetEnumerator()) {
        $requiredLines += "$($entry.Key)=$($entry.Value)"
    }
    $matches = $true
    foreach ($line in $requiredLines) {
        if (-not $text.Contains($line, [StringComparison]::Ordinal)) {
            $matches = $false
            break
        }
    }
    [pscustomobject]@{ Exists = $true; Matches = $matches }
}

function Get-CfhVenvOwnerText {
    param(
        [Parameter(Mandatory)][string]$StateRoot,
        [Parameter(Mandatory)][ValidateSet('mcp', 'gateway')][string]$Kind
    )
    $record = [ordered]@{
        version = 1
        owner = 'ClaudeFoundryHybrid'
        kind = $Kind
        stateRoot = [IO.Path]::GetFullPath($StateRoot)
    }
    ($record | ConvertTo-Json -Compress) + [Environment]::NewLine
}

function Get-CfhPythonLauncher {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return [pscustomobject]@{ Command = $py.Source; Prefix = @('-3') } }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return [pscustomobject]@{ Command = $python.Source; Prefix = @() } }
    throw 'Python 3 was not found. Install Python 3.10 or later first.'
}

function Invoke-CfhLauncher {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $runtime = Get-CfhPythonLauncher
    $launcher = Join-Path $PSScriptRoot 'launcher.py'
    & $runtime.Command @($runtime.Prefix) $launcher @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Claude Foundry launcher exited with code $LASTEXITCODE." }
}

function ConvertFrom-CfhSecureString {
    param([Parameter(Mandatory)][Security.SecureString]$Value)
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

function Get-CfhSecretValue {
    param(
        [Parameter(Mandatory)][string]$EnvironmentName,
        [Parameter(Mandatory)][string]$Prompt
    )
    $existing = [Environment]::GetEnvironmentVariable($EnvironmentName, 'Process')
    if ($existing) { return $existing }
    ConvertFrom-CfhSecureString (Read-Host -Prompt $Prompt -AsSecureString)
}

function Invoke-CfhPublicPip {
    param(
        [Parameter(Mandatory)][string]$PythonPath,
        [Parameter(Mandatory)][string[]]$Arguments
    )
    if ($Arguments.Count -eq 0 -or $Arguments[0] -ne 'install') {
        throw 'Invoke-CfhPublicPip accepts pip install arguments only.'
    }
    $installArguments = if ($Arguments.Count -gt 1) { @($Arguments[1..($Arguments.Count - 1)]) } else { @() }
    $names = @('PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL', 'PIP_NO_INPUT', 'PIP_CONFIG_FILE')
    $saved = @{}
    foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    try {
        $env:PIP_INDEX_URL = 'https://pypi.org/simple'
        $env:PIP_EXTRA_INDEX_URL = ''
        $env:PIP_NO_INPUT = '1'
        # --isolated ignores user/global pip configuration; the explicit index
        # prevents a machine-level private extra index from entering resolution.
        & $PythonPath -m pip --isolated --disable-pip-version-check install --index-url 'https://pypi.org/simple' --no-input @installArguments
        if ($LASTEXITCODE -ne 0) { throw "pip exited with code $LASTEXITCODE." }
    } finally {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
        }
    }
}

Export-ModuleMember -Function Get-CfhRepositoryRoot, Write-CfhUtf8NoBom, Get-CfhDirectoryHash, Get-CfhTextHash, Test-CfhPathWithin, Get-CfhMcpConfig, Get-CfhMcpRegistrationState, Get-CfhVenvOwnerText, Get-CfhPythonLauncher, Invoke-CfhLauncher, ConvertFrom-CfhSecureString, Get-CfhSecretValue, Invoke-CfhPublicPip
