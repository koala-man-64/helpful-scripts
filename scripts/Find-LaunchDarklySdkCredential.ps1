#requires -Version 5.1

<#
.SYNOPSIS
Find the owner of one exposed LaunchDarkly server-side SDK secret (read-only).
.DESCRIPTION
Edit KnownSdkSecret below in a PRIVATE copy, then run:
  powershell.exe -NoProfile -File .\Find-LaunchDarklySdkCredential.ps1
Or use pwsh -NoProfile -File .\Find-LaunchDarklySdkCredential.ps1.
Get-Help .\Find-LaunchDarklySdkCredential.ps1 -Full displays these instructions.

The script reads LD_ACCESS_TOKEN from the process environment. If absent it
prompts securely for a separate LaunchDarkly management REST API access token.
Have the authorized LaunchDarkly team supply a token that can list projects,
environments and SDK keys AND reveal SDK-key values in the relevant organization.
The exposed sdk- value is NOT that management token. A VIT or incident number
does not supply API access. Do not put either credential in a command line, chat,
ticket, transcript, debug trace, or source control. Distribute the placeholder
copy; configure the secret only on the authorized operator's machine. Managed
PowerShell script-block logging may capture edited source; follow local policy.

Uses GET only against the US commercial API at https://app.launchdarkly.com.
Does not delete, rotate, revoke, or test SDK authentication. No external modules.
One token searches only its own organization and accessible resources, not other
organizations, hidden projects, other regions, or credentials already deleted.
Rerun with an authorized token for each candidate organization. A complete scan
is not a consistent snapshot: avoid concurrent key changes and rerun if needed.

Output: matching project/environment names and keys, SDK credential name and
resource key (NOT its secret value), default status, and owner navigation steps.
Only sanitized metadata and fixed error categories are printed; response bodies,
SDK values, management tokens and authorization headers are never printed.
An inaccessible/failed page, missing or masked SDK value, repeated item, changing
SDK total, or safety limit makes the scan INCOMPLETE, even if matches were found.
Previously discovered resources are still scanned after a later page fails.

Exit codes: 0 = match(es), accessible scan complete; 1 = no match, accessible
scan complete; 2 = incomplete (matches may exist); 3 = configuration/setup error.
The authorized owner should use Organization settings > Security > SDK keys,
select the reported project/environment, and locate the reported credential.
Use the organization's incident/revocation process; discovery is not revocation.

API references verified 2026-09-11:
https://launchdarkly.com/docs/api/projects/get-projects
https://launchdarkly.com/docs/api/environments/get-environments-by-project
https://launchdarkly.com/docs/api/sdk-keys-beta/get-sdk-keys
https://launchdarkly.com/docs/home/account/environment/keys
All three collections support limit/offset. SDK keys require LD-API-Version:
beta and expose items[].value separately from items[].key, name and isDefault.
SDK queries filter kind:sdk without an active filter (include expired keys).
Server-provided pagination links are never followed: offsets are built locally,
on the fixed origin. Projects/environments continue until an empty page, even
after short pages. SDK pages use the documented totalCount. Duplicate keys and
page limits prevent loops when a server ignores offset. No legacy apiKey fallback
can establish the name/resource key of every additional SDK credential.
#>
param()

# ----------------------- OPERATOR CONFIGURATION -----------------------
$KnownSdkSecret = 'REPLACE_WITH_EXPOSED_SDK_SECRET'
$ManagementTokenEnvironmentVariable = 'LD_ACCESS_TOKEN'
$RequestTimeoutSeconds = 30
$MaxRetries = 4                 # Per GET, in addition to the initial attempt.
$MaxRetryDelaySeconds = 60      # Larger server Retry-After => incomplete; no early retry.
$MaxPagesPerCollection = 10000
# --------------------- END OPERATOR CONFIGURATION ---------------------

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$VerbosePreference = 'SilentlyContinue'
$DebugPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
$ApiOrigin = 'https://app.launchdarkly.com'
$ManagementToken = $null
$Client = $null
$Handler = $null

function Get-Field($Object, [string]$Name) {
    if ($null -ne $Object) {
        $property = $Object.PSObject.Properties[$Name]
        if ($null -ne $property) { return $property.Value }
    }
    return $null
}

function Test-ReadableSdkValue($Value) {
    # Be conservative about common redaction sentinels, even if a literal key
    # could theoretically have this suffix. Such values cannot prove no-match.
    return ($Value -is [string] -and $Value -cmatch '^sdk-[A-Za-z0-9-]+$' -and
        $Value -notmatch '^sdk-(redacted|masked|hidden|removed|unavailable|x[-x]*|0[-0]*)$')
}

function Protect-Text([string]$Text) {
    if ($null -eq $Text) { $Text = '' }
    # Sanitize metadata too: names are user-controlled and can contain secrets.
    foreach ($secret in @($KnownSdkSecret, $ManagementToken)) {
        if (-not [string]::IsNullOrEmpty($secret)) {
            $Text = $Text.Replace($secret, '[REDACTED]')
            $Text = $Text.Replace([uri]::EscapeDataString($secret), '[REDACTED]')
        }
    }
    $Text = [regex]::Replace($Text, '(?i)(sdk|mob)-[^\s"''<>]*', '[REDACTED]')
    return [regex]::Replace($Text, '[\x00-\x1f\x7f-\x9f]', ' ')
}

function Invoke-LdGet([string]$PathAndQuery) {
    # Only locally generated paths reach here; no response link can change host.
    if (-not $PathAndQuery.StartsWith('/api/v2/projects', [StringComparison]::Ordinal)) {
        return @{ Ok = $false; Reason = 'unsafe-path' }
    }
    for ($attempt = 0; $attempt -le $MaxRetries; $attempt++) {
        $request = $null
        $response = $null
        $retry = $false
        $delay = [math]::Min($MaxRetryDelaySeconds, [math]::Pow(2, $attempt))
        $reason = 'transport-or-timeout'
        try {
            $request = [System.Net.Http.HttpRequestMessage]::new(
                [System.Net.Http.HttpMethod]::Get, ($ApiOrigin + $PathAndQuery))
            $request.Headers.Add('Authorization', $ManagementToken)
            $request.Headers.Add('LD-API-Version', 'beta')
            $request.Headers.Add('Accept', 'application/json')
            $response = $Client.SendAsync($request).GetAwaiter().GetResult()
            $status = [int]$response.StatusCode
            if ($status -eq 200) {
                try {
                    $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                    $data = ConvertFrom-Json -InputObject $body -ErrorAction Stop
                    return @{ Ok = $true; Data = $data }
                }
                catch { return @{ Ok = $false; Reason = 'invalid-json' } }
                finally { $body = $null }
            }
            # Never inspect or print a non-200 body or exception message.
            $reason = 'HTTP-' + $status
            $retry = $status -in @(408, 429, 500, 502, 503, 504)
            if ($retry -and $null -ne $response.Headers.RetryAfter) {
                $after = $response.Headers.RetryAfter
                if ($null -ne $after.Delta) { $delay = $after.Delta.TotalSeconds }
                elseif ($null -ne $after.Date) {
                    $delay = ($after.Date - [DateTimeOffset]::UtcNow).TotalSeconds
                }
                $delay = [math]::Max(0, [math]::Ceiling($delay))
                if ($delay -gt $MaxRetryDelaySeconds) {
                    return @{ Ok = $false; Reason = ($reason + '-retry-delay-exceeds-bound') }
                }
            }
        }
        catch { $retry = $true } # Exception text can contain credentials/response data.
        finally {
            if ($null -ne $response) { $response.Dispose() }
            if ($null -ne $request) { $request.Dispose() }
        }
        if (-not $retry -or $attempt -eq $MaxRetries) {
            return @{ Ok = $false; Reason = $reason }
        }
        Start-Sleep -Milliseconds ([int]([math]::Max(1, $delay) * 1000))
    }
}

function Get-LdCollection([string]$Path, [switch]$SdkKeys) {
    $items = [System.Collections.Generic.List[object]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $offset = 0
    $expectedTotal = $null
    $reason = 'page-limit'
    $complete = $false
    for ($page = 0; $page -lt $MaxPagesPerCollection; $page++) {
        $query = '?limit=100&offset=' + $offset
        if ($SdkKeys) { $query += '&filter=kind%3Asdk' }
        $result = Invoke-LdGet ($Path + $query)
        if (-not $result.Ok) { $reason = $result.Reason; break }
        # JSON [] is valid. Null/missing/items-as-object is not proof of emptiness.
        if ($null -eq $result.Data) { $reason = 'invalid-items'; break }
        $itemsProperty = $result.Data.PSObject.Properties['items']
        if ($null -eq $itemsProperty -or $null -eq $itemsProperty.Value -or
            $itemsProperty.Value -isnot [array]) {
            $reason = 'invalid-items'; break
        }
        $pageItems = @($itemsProperty.Value)
        $total = Get-Field $result.Data 'totalCount'
        if ($SdkKeys) {
            if (($total -isnot [int] -and $total -isnot [long]) -or $total -lt 0) {
                $reason = 'invalid-sdk-total'; break
            }
            if ($null -ne $expectedTotal -and $total -ne $expectedTotal) {
                $reason = 'sdk-total-changed'; break
            }
            $expectedTotal = $total
        }
        $invalid = $false
        foreach ($item in $pageItems) {
            $key = Get-Field $item 'key'
            if ($key -isnot [string] -or [string]::IsNullOrWhiteSpace($key) -or $key -in @('.', '..')) {
                $invalid = $true; $reason = 'missing-resource-key'; continue
            }
            if (-not $seen.Add($key)) {
                $invalid = $true; $reason = 'repeated-resource-or-pagination-loop'; continue
            }
            $items.Add($item)
        }
        if ($invalid) { break }
        $offset += $pageItems.Count
        if ($SdkKeys -and $offset -gt $expectedTotal) { $reason = 'sdk-total-mismatch'; break }
        if ($SdkKeys -and $offset -eq $expectedTotal) { $complete = $true; break }
        if ($pageItems.Count -eq 0) {
            if ($SdkKeys) { $reason = 'premature-empty-sdk-page' }
            else { $complete = $true }
            break
        }
    }
    return @{ Items = $items.ToArray(); Complete = $complete; Reason = $reason }
}

function Write-ScanFailure([string]$Scope, [string]$Reason) {
    Write-Output ('INCOMPLETE: ' + (Protect-Text $Scope) + ' [' + $Reason + ']')
}

$exitCode = 3
$oldTls = [Net.ServicePointManager]::SecurityProtocol
try {
    if (-not (Test-ReadableSdkValue $KnownSdkSecret) -or
        $RequestTimeoutSeconds -lt 1 -or $RequestTimeoutSeconds -gt 300 -or
        $MaxRetries -lt 0 -or $MaxRetries -gt 10 -or
        $MaxRetryDelaySeconds -lt 1 -or $MaxRetryDelaySeconds -gt 60 -or
        $MaxPagesPerCollection -lt 1 -or $MaxPagesPerCollection -gt 10000) {
        Write-Output 'CONFIGURATION ERROR: set the exposed SDK secret and valid bounded configuration.'
        exit 3
    }
    $ManagementToken = [Environment]::GetEnvironmentVariable($ManagementTokenEnvironmentVariable)
    if ([string]::IsNullOrWhiteSpace($ManagementToken)) {
        $secureToken = Read-Host 'LaunchDarkly management API token (hidden)' -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
        try { $ManagementToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            $secureToken.Dispose()
        }
    }
    if ([string]::IsNullOrWhiteSpace($ManagementToken) -or $ManagementToken -match '\s' -or
        $ManagementToken.StartsWith('sdk-', [StringComparison]::OrdinalIgnoreCase)) {
        Write-Output 'CONFIGURATION ERROR: supply a separate management REST API token.'
        exit 3
    }
    Add-Type -AssemblyName System.Net.Http
    [Net.ServicePointManager]::SecurityProtocol = $oldTls -bor [Net.SecurityProtocolType]::Tls12
    $Handler = [System.Net.Http.HttpClientHandler]::new()
    $Handler.AllowAutoRedirect = $false
    $Handler.UseCookies = $false
    $Client = [System.Net.Http.HttpClient]::new($Handler)
    $Client.Timeout = [TimeSpan]::FromSeconds($RequestTimeoutSeconds)
    $Client.MaxResponseContentBufferSize = 16MB
    $exitCode = 2
    $complete = $true
    $matchCount = 0
    $environmentCount = 0
    $credentialCount = 0
    Write-Output 'Scanning accessible resources in this token organization (US commercial API); GET only.'
    $projects = Get-LdCollection '/api/v2/projects'
    if (-not $projects.Complete) {
        $complete = $false; Write-ScanFailure 'projects' $projects.Reason
    }
    foreach ($project in $projects.Items) {
        $projectPath = '/api/v2/projects/' + [uri]::EscapeDataString($project.key)
        $environments = Get-LdCollection ($projectPath + '/environments')
        if (-not $environments.Complete) {
            $complete = $false; Write-ScanFailure ('project ' + $project.key) $environments.Reason
        }
        foreach ($environment in $environments.Items) {
            $environmentCount++
            $scope = 'project ' + $project.key + ', environment ' + $environment.key
            $environmentPath = $projectPath + '/environments/' + [uri]::EscapeDataString($environment.key)
            $credentials = Get-LdCollection ($environmentPath + '/sdk-keys') -SdkKeys
            if (-not $credentials.Complete) {
                $complete = $false; Write-ScanFailure $scope $credentials.Reason
            }
            foreach ($credential in $credentials.Items) {
                $credentialCount++
                $value = Get-Field $credential 'value'
                if ((Get-Field $credential 'kind') -cne 'sdk' -or -not (Test-ReadableSdkValue $value)) {
                    $complete = $false
                    Write-ScanFailure $scope 'missing-masked-or-invalid-sdk-value'
                    continue
                }
                if ([string]::Equals($value, $KnownSdkSecret, [StringComparison]::Ordinal)) {
                    $matchCount++
                    $name = Get-Field $credential 'name'
                    $isDefault = Get-Field $credential 'isDefault'
                    if ($name -isnot [string] -or [string]::IsNullOrWhiteSpace($name) -or
                        $isDefault -isnot [bool]) {
                        $complete = $false
                        Write-ScanFailure $scope 'missing-match-metadata'
                    }
                    Write-Output ('MATCH {0}' -f $matchCount)
                    Write-Output ('  Project: {0} | key: {1}' -f
                        (Protect-Text (Get-Field $project 'name')), (Protect-Text $project.key))
                    Write-Output ('  Environment: {0} | key: {1}' -f
                        (Protect-Text (Get-Field $environment 'name')), (Protect-Text $environment.key))
                    Write-Output ('  SDK credential: {0} | resource key: {1} | isDefault: {2}' -f
                        (Protect-Text $name), (Protect-Text $credential.key), (Protect-Text ([string]$isDefault)))
                    Write-Output ('  Owner location: https://app.launchdarkly.com > Organization settings > Security > SDK keys; select the project/environment above.')
                    Write-Output ('  GET resource path (management API): ' +
                        (Protect-Text ($environmentPath + '/sdk-keys/' + [uri]::EscapeDataString($credential.key))))
                }
                $value = $null
            }
        }
    }
    Write-Output ('Counts: {0} projects, {1} environments, {2} SDK credentials, {3} matches.' -f
        $projects.Items.Count, $environmentCount, $credentialCount, $matchCount)
    if (-not $complete) {
        Write-Output 'INCOMPLETE: unresolved pages/values remain. No-match is inconclusive; resolve reported access/API failures and rerun.'
        $exitCode = 2
    }
    elseif ($matchCount -eq 0) {
        Write-Output 'NOT FOUND: complete accessible scan only. Hidden resources, other organizations/regions and deleted credentials were not searched.'
        $exitCode = 1
    }
    else {
        Write-Output 'FOUND: complete accessible scan. Give the sanitized match details to the authorized owner; no credentials were changed.'
        $exitCode = 0
    }
}
catch {
    # Do not interpolate $_, ErrorDetails, exception text, URLs or raw responses.
    Write-Output 'FAILED: setup or unexpected scan error; no complete-scan claim. Error details suppressed to protect credentials.'
}
finally {
    if ($null -ne $Client) { $Client.Dispose() }
    if ($null -ne $Handler) { $Handler.Dispose() }
    [Net.ServicePointManager]::SecurityProtocol = $oldTls
    $ManagementToken = $null
    $KnownSdkSecret = $null
}
exit $exitCode
