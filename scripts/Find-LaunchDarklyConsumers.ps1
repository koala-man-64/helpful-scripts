#requires -Version 7.0
<#
.SYNOPSIS
Collect a scoped, read-only LaunchDarkly consumer-discovery inventory.
.DESCRIPTION
Requires a JSON configuration of explicit cloud/repository scopes. Uses existing
az, gcloud, gh sessions and LD_ACCESS_TOKEN (or a hidden prompt). Reads the known
SDK secret from LD_KNOWN_SDK_SECRET or a hidden prompt. No secret arguments.
Reports environment activity, exact stored/source matches, references, and gaps;
never claims a complete runtime consumer list. No key rotation or cloud mutation.
See Find-LaunchDarklyConsumers.README.md for permissions, output, and limitations.
.PARAMETER ConfigPath
Path to non-secret JSON configuration. See the companion example.
.PARAMETER OutputDirectory
New report directory in approved private storage. Existing reports are not overwritten.
.PARAMETER From
Beginning of the LaunchDarkly observation window (UTC recommended).
.PARAMETER To
End of the observation window, up to now. Maximum range: 365 days.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ConfigPath,
    [string]$OutputDirectory = (Join-Path (Get-Location) ('launchdarkly-inventory-' + [datetime]::UtcNow.ToString('yyyyMMdd-HHmmss'))),
    [datetimeoffset]$From = [datetimeoffset]::UtcNow.AddDays(-30),
    [datetimeoffset]$To = [datetimeoffset]::UtcNow
)
$ErrorActionPreference='Stop'; $VerbosePreference='SilentlyContinue'; $DebugPreference='SilentlyContinue'; $ProgressPreference='SilentlyContinue'
. "$PSScriptRoot/launchdarkly-consumers/Core.ps1"
. "$PSScriptRoot/launchdarkly-consumers/Configuration.ps1"
. "$PSScriptRoot/launchdarkly-consumers/LaunchDarkly.ps1"
. "$PSScriptRoot/launchdarkly-consumers/Cloud.ps1"
. "$PSScriptRoot/launchdarkly-consumers/Repositories.ps1"
$code=3
try {
    $config=Read-LdcConfiguration $ConfigPath
    if ($From -ge $To -or ($To-$From).TotalDays -gt 365 -or $To -gt [datetimeoffset]::UtcNow.AddMinutes(1)) { throw 'LDC:invalid-observation-window' }
    # Validate output before prompting or reading any cloud secrets.
    $target=[IO.Path]::GetFullPath($OutputDirectory)
    foreach ($name in @('launchdarkly-consumers.json','launchdarkly-consumers.csv')) {
        if (Test-Path -LiteralPath (Join-Path $target $name)) { throw 'LDC:output-already-exists' }
    }
    $known=Read-LdcPrivateValue LD_KNOWN_SDK_SECRET 'Known SDK secret (hidden)'
    if (-not $known.StartsWith('sdk-', [StringComparison]::Ordinal) -or $known -match '\s') { throw 'LDC:invalid-known-secret' }
    $management=Read-LdcPrivateValue LD_ACCESS_TOKEN 'LaunchDarkly management access token (hidden)'
    if (-not $management -or $management -ceq $known -or $management -match '\s') { throw 'LDC:invalid-management-token' }
    Initialize-LdcState -Config $config -KnownSecret $known -ManagementToken $management -From $From -To $To
    Add-LdcFinding -Platform LaunchDarkly -Scope "$($config.launchDarkly.projectKey)/$($config.launchDarkly.environmentKey)" `
        -Resource $config.launchDarkly.credentialResourceKey -Classification CredentialTarget -Environment $config.launchDarkly.environmentKey `
        -Evidence 'Operator-supplied credential identity from the existing investigator; not independently revalidated here.'
    foreach ($collector in @('Invoke-LdcLaunchDarkly','Invoke-LdcCloud','Invoke-LdcRepositories')) {
        try { & $collector }
        catch {
            Add-LdcGap -Platform Collector -Scope $collector -Resource $collector -Reason (Get-LdcFailureCode $_) `
                -NextStep 'Review this collector failure; other collectors and earlier findings are retained.'
        }
    }
    $status=Write-LdcReport -OutputDirectory $target
    Write-Output "Inventory status: $status. Runtime consumer completeness is not established."
    Write-Output 'Sanitized reports: launchdarkly-consumers.json and launchdarkly-consumers.csv in the requested output directory.'
    $code=if ($status -eq 'incomplete') {2} else {0}
} catch {
    # Do not emit exception objects, InvocationInfo, raw config, or CLI stderr.
    $reason=Get-LdcFailureCode $_
    Write-Output "Inventory stopped: $reason. No complete consumer list has been established."
    $code=3
} finally {
    $known=$null; $management=$null
    if ($script:Ldc) {
        $script:Ldc.KnownSecret=$null; $script:Ldc.ManagementToken=$null
        $script:Ldc.Tokens.Clear(); $script:Ldc.Sensitive.Clear()
    }
}
exit $code
