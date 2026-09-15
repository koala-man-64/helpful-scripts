#requires -Version 7.2
<#
.SYNOPSIS
Find stored copies and static references to a known Terraform Cloud API token.
.DESCRIPTION
Read-only scoped inventory using Terraform Cloud APIs and existing az/gcloud/gh
sessions. TFC_KNOWN_TOKEN supplies the token to locate, or a hidden prompt is used.
Optional TFC_ACCESS_TOKEN supplies separate inventory access; otherwise the known
token is used. Never pass credentials in arguments or the configuration file.
See Find-TerraformCloudConsumers.README.md for evidence boundaries and permissions.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ConfigPath,
    [string]$OutputDirectory = (Join-Path (Get-Location) ('terraform-inventory-' + [datetime]::UtcNow.ToString('yyyyMMdd-HHmmss'))),
    [datetimeoffset]$From = [datetimeoffset]::UtcNow.AddDays(-30),
    [datetimeoffset]$To = [datetimeoffset]::UtcNow
)
$ErrorActionPreference = 'Stop'
$VerbosePreference = 'SilentlyContinue'; $DebugPreference = 'SilentlyContinue'; $ProgressPreference = 'SilentlyContinue'
$code = 3
try {
    # Both companions reuse the same transport/cloud/CI security fixes.
    foreach ($file in @('Core','Configuration','Cloud','Repositories')) { . "$PSScriptRoot/launchdarkly-consumers/$file.ps1" }
    . "$PSScriptRoot/terraform-consumers/TerraformCloud.ps1"
    $config = Read-LdcConfiguration -Path $ConfigPath -CredentialKind TerraformCloud
    if ($From -ge $To -or ($To - $From).TotalDays -gt 365 -or $To -gt [datetimeoffset]::UtcNow.AddMinutes(1)) { throw 'LDC:invalid-observation-window' }
    $target = [IO.Path]::GetFullPath($OutputDirectory)
    foreach ($name in @('terraform-cloud-consumers.json','terraform-cloud-consumers.csv')) {
        if (Test-Path -LiteralPath (Join-Path $target $name)) { throw 'LDC:output-already-exists' }
    }
    $known = Read-LdcPrivateValue TFC_KNOWN_TOKEN 'Known Terraform Cloud token (hidden)'
    if (!$known -or $known.Length -gt 16384 -or $known -match '\s|[\x00-\x1f\x7f]') { throw 'LDC:invalid-known-token' }
    $management = [Environment]::GetEnvironmentVariable('TFC_ACCESS_TOKEN','Process')
    if (!$management) { $management = $known }
    if ($management.Length -gt 16384 -or $management -match '\s|[\x00-\x1f\x7f]') { throw 'LDC:invalid-inventory-token' }
    Initialize-LdcState -Config $config -KnownSecret $known -ManagementToken $management -From $From -To $To -CredentialKind TerraformCloud
    foreach ($collector in @('Invoke-LdcTerraformCloud','Invoke-LdcCloud','Invoke-LdcRepositories')) {
        try { & $collector }
        catch { Add-LdcGap -Platform Collector -Scope $collector -Resource $collector -Reason (Get-LdcFailureCode $_) }
    }
    $status = Write-LdcReport -OutputDirectory $target
    Write-Output "Inventory status: $status. Runtime token consumption is not established."
    Write-Output 'Sanitized reports: terraform-cloud-consumers.json and terraform-cloud-consumers.csv in the requested directory.'
    $code = if ($status -eq 'incomplete') { 2 } else { 0 }
} catch {
    # Never emit exception records: they can contain credential-bearing input.
    $reason = 'setup-failed'
    if (Get-Command Get-LdcFailureCode -ErrorAction SilentlyContinue) { $reason = Get-LdcFailureCode $_ }
    Write-Output "Inventory stopped: $reason. No complete consumer list has been established."
} finally {
    $known = $null; $management = $null
    if ($script:Ldc) {
        $script:Ldc.KnownSecret = $null; $script:Ldc.ManagementToken = $null
        $script:Ldc.Tokens.Clear(); $script:Ldc.Sensitive.Clear()
    }
}
exit $code
