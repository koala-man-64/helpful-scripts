[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Apply,
    [switch]$Overwrite,
    [switch]$IncludeGatewayLab,
    [switch]$UseExistingVenvs,
    [ValidatePattern('^3\.(10|11|12|13|14)$')][string]$McpPythonVersion = '3.10',
    [ValidatePattern('^3\.(11|12|13)$')][string]$GatewayPythonVersion = '3.11',
    [string]$GatewayPythonCommand,
    [string]$McpServerName = 'foundry-model-consult',
    [string]$SkillRoot = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.claude\skills'),
    [string]$StateRoot = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ClaudeFoundryHybrid')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'HybridTools.psm1') -Force

$repoRoot = Get-CfhRepositoryRoot
$mcpRoot = Join-Path $repoRoot 'mcp-chatbot'
$mcpVenv = Join-Path $mcpRoot '.venv'
$mcpPython = Join-Path $mcpVenv 'Scripts\python.exe'
$skillSource = Join-Path $repoRoot 'claude-foundry-hybrid\profile\foundry-model-bench'
$skillTarget = Join-Path $SkillRoot 'foundry-model-bench'
$manifestPath = Join-Path $StateRoot 'install.json'
$gatewayVenv = Join-Path $repoRoot 'claude-foundry-hybrid\gateway\.venv'
$gatewayPython = Join-Path $gatewayVenv 'Scripts\python.exe'
$gatewayLock = Join-Path $repoRoot 'claude-foundry-hybrid\gateway\requirements.lock'
$gatewaySbom = Join-Path $StateRoot 'gateway-sbom.txt'
$mcpOwnerMarker = Join-Path $mcpVenv '.claude-foundry-hybrid-owner.json'
$gatewayOwnerMarker = Join-Path $gatewayVenv '.claude-foundry-hybrid-owner.json'
$mcpOwnerText = Get-CfhVenvOwnerText -StateRoot $StateRoot -Kind mcp
$gatewayOwnerText = Get-CfhVenvOwnerText -StateRoot $StateRoot -Kind gateway

foreach ($required in @($mcpRoot, $skillSource)) {
    if (-not (Test-Path -LiteralPath $required -PathType Container)) { throw "Missing package content: $required" }
}
if ($IncludeGatewayLab -and -not (Test-Path -LiteralPath $gatewayLock -PathType Leaf)) {
    throw "Missing gateway lock: $gatewayLock"
}
$null = Get-Command claude -ErrorAction Stop
$null = Get-Command py -ErrorAction Stop

$existingManifest = if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
} else { $null }
if ($existingManifest) {
    if ($existingManifest.version -ne 1) { throw "Unsupported ownership manifest version: $($existingManifest.version)" }
    if ($existingManifest.mcpServerName -ne $McpServerName) {
        throw "State root already owns MCP server '$($existingManifest.mcpServerName)'. Use its original name or a different -StateRoot."
    }
    if ([IO.Path]::GetFullPath($existingManifest.mcpPython) -ne [IO.Path]::GetFullPath($mcpPython)) {
        throw 'State root belongs to another checkout. Uninstall from that checkout or use a different -StateRoot.'
    }
}

$mcpVenvExisted = Test-Path -LiteralPath $mcpPython -PathType Leaf
$gatewayVenvExisted = Test-Path -LiteralPath $gatewayPython -PathType Leaf
$gatewayCreator = $null
$gatewayCreatorPrefix = @()
if ($IncludeGatewayLab -and -not $gatewayVenvExisted) {
    if ($GatewayPythonCommand) {
        $gatewayCreator = (Get-Command $GatewayPythonCommand -ErrorAction Stop).Source
    } else {
        $gatewayCreator = (Get-Command py -ErrorAction Stop).Source
        $gatewayCreatorPrefix = @("-$GatewayPythonVersion")
    }
    $actualGatewayVersion = ((& $gatewayCreator @($gatewayCreatorPrefix) -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")') -join '').Trim()
    if ($LASTEXITCODE -ne 0 -or $actualGatewayVersion -ne $GatewayPythonVersion) {
        throw "Gateway Python must resolve to $GatewayPythonVersion; got '$actualGatewayVersion'."
    }
}
$mcpMarkerOwned = (Test-Path -LiteralPath $mcpOwnerMarker -PathType Leaf) -and ((Get-Content -LiteralPath $mcpOwnerMarker -Raw) -eq $mcpOwnerText)
$gatewayMarkerOwned = (Test-Path -LiteralPath $gatewayOwnerMarker -PathType Leaf) -and ((Get-Content -LiteralPath $gatewayOwnerMarker -Raw) -eq $gatewayOwnerText)
$manifestOwnsMcpVenv = [bool]($existingManifest -and $existingManifest.mcpVenvCreated)
$manifestOwnsGatewayVenv = [bool]($existingManifest -and $existingManifest.gatewayVenvCreated)
if ($manifestOwnsMcpVenv -and -not $mcpMarkerOwned) {
    throw 'The owned MCP venv marker is missing or changed; refusing to mutate that environment.'
}
if ($manifestOwnsGatewayVenv -and -not $gatewayMarkerOwned) {
    throw 'The owned gateway venv marker is missing or changed; refusing to mutate that environment.'
}
if ($mcpVenvExisted -and -not $mcpMarkerOwned -and -not $existingManifest -and -not $UseExistingVenvs) {
    throw "An unowned MCP venv already exists at $mcpVenv. Use -UseExistingVenvs to install into it without claiming deletion ownership."
}
if ($IncludeGatewayLab -and $gatewayVenvExisted -and -not $gatewayMarkerOwned -and -not $existingManifest -and -not $UseExistingVenvs) {
    throw "An unowned gateway venv already exists at $gatewayVenv. Use -UseExistingVenvs to install into it without claiming deletion ownership."
}
$sourceSkillHash = Get-CfhDirectoryHash $skillSource
$skillBackup = $null
$skillBackupHash = $null
$expectedMcpConfig = Get-CfhMcpConfig $mcpPython
$mcpJson = $expectedMcpConfig | ConvertTo-Json -Depth 8 -Compress
$mcpConfigHash = Get-CfhTextHash $mcpJson
$mcpState = Get-CfhMcpRegistrationState -Name $McpServerName -ExpectedConfig $expectedMcpConfig
$ownedRegistration = [bool]($existingManifest -and $existingManifest.mcpServerName -eq $McpServerName)
if ($mcpState.Exists -and (-not $ownedRegistration -or -not $mcpState.Matches)) {
    throw "MCP server '$McpServerName' exists but does not exactly match a registration owned by this installer."
}

if (Test-Path -LiteralPath $skillTarget -PathType Container) {
    $targetHash = Get-CfhDirectoryHash $skillTarget
    if ($targetHash -ne $sourceSkillHash -and -not $Overwrite) {
        throw "Existing skill differs: $skillTarget. Use -Overwrite to back it up and replace it."
    }
}

Write-Host "MCP runtime: $mcpPython"
Write-Host "Claude skill: $skillTarget"
Write-Host "MCP server: $McpServerName (user scope)"
if ($IncludeGatewayLab) { Write-Host "Gateway lab runtime: $gatewayVenv" }
if (-not $Apply) {
    Write-Host 'Dry run only. Re-run with -Apply to install these artifacts.'
    return
}

if (-not $mcpVenvExisted) {
    if ($PSCmdlet.ShouldProcess($mcpVenv, 'Create isolated mcp-chatbot virtual environment')) {
        & py "-$McpPythonVersion" -m venv $mcpVenv
        if ($LASTEXITCODE -ne 0) { throw "Could not create mcp-chatbot venv with Python $McpPythonVersion." }
        Write-CfhUtf8NoBom -Path $mcpOwnerMarker -Text $mcpOwnerText
    }
}
if ($PSCmdlet.ShouldProcess($mcpRoot, 'Install mcp-chatbot into its isolated environment')) {
    $mcpEditable = $mcpRoot + '[dev]'
    Invoke-CfhPublicPip -PythonPath $mcpPython -Arguments @('install', '-e', $mcpEditable)
}

if (Test-Path -LiteralPath $skillTarget -PathType Container) {
    $targetHash = Get-CfhDirectoryHash $skillTarget
    if ($targetHash -ne $sourceSkillHash) {
        if (-not (Test-CfhPathWithin -Path $skillTarget -Root $SkillRoot)) { throw "Unsafe skill target: $skillTarget" }
        $skillBackup = Join-Path $StateRoot ("backups\foundry-model-bench-" + (Get-Date -Format yyyyMMddHHmmss))
        $skillBackupHash = $targetHash
        if ($PSCmdlet.ShouldProcess($skillTarget, "Move existing skill to $skillBackup")) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $skillBackup) -Force | Out-Null
            Move-Item -LiteralPath $skillTarget -Destination $skillBackup
        }
    }
}
if (-not (Test-Path -LiteralPath $skillTarget -PathType Container)) {
    if ($PSCmdlet.ShouldProcess($skillTarget, 'Install Claude Code skill')) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $skillTarget) -Force | Out-Null
        Copy-Item -LiteralPath $skillSource -Destination $skillTarget -Recurse
    }
}

if (-not $mcpState.Exists) {
    if ($PSCmdlet.ShouldProcess($McpServerName, 'Register user-scope Claude MCP server')) {
        & claude mcp add-json --scope user $McpServerName $mcpJson
        if ($LASTEXITCODE -ne 0) { throw "Claude MCP registration failed with code $LASTEXITCODE." }
        $mcpState = Get-CfhMcpRegistrationState -Name $McpServerName -ExpectedConfig $expectedMcpConfig
        if (-not $mcpState.Exists -or -not $mcpState.Matches) {
            throw 'Claude MCP registration was written but did not pass exact read-back verification.'
        }
    }
}

if ($IncludeGatewayLab) {
    if (-not $gatewayVenvExisted) {
        if ($PSCmdlet.ShouldProcess($gatewayVenv, 'Create isolated LiteLLM virtual environment')) {
            & $gatewayCreator @($gatewayCreatorPrefix) -m venv $gatewayVenv
            if ($LASTEXITCODE -ne 0) { throw "Could not create gateway venv with Python $GatewayPythonVersion." }
            Write-CfhUtf8NoBom -Path $gatewayOwnerMarker -Text $gatewayOwnerText
        }
    }
    if ($PSCmdlet.ShouldProcess($gatewayVenv, 'Install hash-locked LiteLLM lab dependencies')) {
        Invoke-CfhPublicPip -PythonPath $gatewayPython -Arguments @('install', '--require-hashes', '--only-binary=:all:', '-r', $gatewayLock)
        & $gatewayPython -m pip check
        if ($LASTEXITCODE -ne 0) { throw 'Gateway dependency check failed.' }
        & $gatewayPython -c 'from litellm import run_server'
        if ($LASTEXITCODE -ne 0) { throw 'Gateway LiteLLM entrypoint import failed.' }
        $indicator = Get-ChildItem -LiteralPath $gatewayVenv -Recurse -Filter 'litellm_init.pth' -File -ErrorAction SilentlyContinue
        if ($indicator) { throw "Compromise indicator found in gateway venv: $($indicator.FullName)" }
    }
}

$priorMcpVenvOwned = [bool]($existingManifest -and $existingManifest.mcpVenvCreated)
$priorGatewayIncluded = [bool]($existingManifest -and $existingManifest.gatewayIncluded)
$priorGatewayVenvOwned = [bool]($existingManifest -and $existingManifest.gatewayVenvCreated)
$priorSkillBackup = if ($existingManifest -and $existingManifest.PSObject.Properties.Name -contains 'skillBackup') { $existingManifest.skillBackup } else { $null }
$priorSkillBackupHash = if ($existingManifest -and $existingManifest.PSObject.Properties.Name -contains 'skillBackupHash') { $existingManifest.skillBackupHash } else { $null }
$manifest = [ordered]@{
    version = 1
    installedAt = [DateTimeOffset]::Now.ToString('o')
    mcpServerName = $McpServerName
    mcpPython = [IO.Path]::GetFullPath($mcpPython)
    mcpConfigHash = $mcpConfigHash
    mcpVenvCreated = [bool]($priorMcpVenvOwned -or $mcpMarkerOwned -or -not $mcpVenvExisted)
    skillRoot = [IO.Path]::GetFullPath($SkillRoot)
    skillTarget = [IO.Path]::GetFullPath($skillTarget)
    skillHash = $sourceSkillHash
    skillBackup = if ($skillBackup) { [IO.Path]::GetFullPath($skillBackup) } else { $priorSkillBackup }
    skillBackupHash = if ($skillBackupHash) { $skillBackupHash } else { $priorSkillBackupHash }
    gatewayIncluded = [bool]($priorGatewayIncluded -or $IncludeGatewayLab)
    gatewayVenv = [IO.Path]::GetFullPath($gatewayVenv)
    gatewayVenvCreated = [bool]($priorGatewayVenvOwned -or $gatewayMarkerOwned -or ($IncludeGatewayLab -and -not $gatewayVenvExisted))
    gatewayLockHash = if ($IncludeGatewayLab) { (Get-FileHash -LiteralPath $gatewayLock -Algorithm SHA256).Hash.ToLowerInvariant() } elseif ($existingManifest) { $existingManifest.gatewayLockHash } else { $null }
    gatewaySbom = [IO.Path]::GetFullPath($gatewaySbom)
}
if ($PSCmdlet.ShouldProcess($manifestPath, 'Write nonsecret ownership manifest')) {
    Write-CfhUtf8NoBom -Path $manifestPath -Text (($manifest | ConvertTo-Json -Depth 8) + [Environment]::NewLine)
    if ($IncludeGatewayLab) {
        $freeze = & $gatewayPython -m pip freeze --all
        if ($LASTEXITCODE -ne 0) { throw 'Could not record gateway dependency inventory.' }
        Write-CfhUtf8NoBom -Path $gatewaySbom -Text (($freeze -join [Environment]::NewLine) + [Environment]::NewLine)
    }
}
Write-Host 'Claude Foundry hybrid kit installed. No API key was persisted.'
