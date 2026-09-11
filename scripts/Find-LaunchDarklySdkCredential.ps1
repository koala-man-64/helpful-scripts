#requires -Version 5.1

<#
.SYNOPSIS
Find the owner of one exposed LaunchDarkly server-side SDK secret (read-only).
.DESCRIPTION
Edit KnownSdkSecret below in a PRIVATE copy, then run:
  powershell.exe -NoProfile -File .\Find-LaunchDarklySdkCredential.ps1
Or use pwsh -NoProfile -File .\Find-LaunchDarklySdkCredential.ps1.
Get-Help .\Find-LaunchDarklySdkCredential.ps1 -Full displays these instructions.

First calls /api/v2/caller-identity with the exposed SDK value in the Authorization
header and prints only sanitized identity fields returned by LaunchDarkly.
Use -IdentityOnly to stop after this lookup without a management token or prompt.
Identity fields are optional; IDs are not project/environment keys or proof of
the SDK credential record name. A 401 does not identify the owner or prove revocation.
By default, continue with the full discovery scan even if this lookup fails.
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
Does not delete, rotate, revoke, or initialize an SDK. The identity request does
authenticate the exposed credential to that endpoint. No external modules.
One token searches only its own organization and accessible resources, not other
organizations, hidden projects, other regions, or credentials already deleted.
Rerun with an authorized token for each candidate organization. A complete scan
is not a consistent snapshot: avoid concurrent key changes and rerun if needed.

Output: matching project/environment names and keys, SDK credential name and
resource key (NOT its secret value), default status, and owner navigation steps.
Each match also includes allowlisted SDK/project/environment metadata, a creator
member lookup, and resource-scoped retained audit history (up to MaxAuditPages).
Missing fields are reported, not treated as false/zero. Audit management changes
are not SDK usage logs. Creator identity is not proof of current ownership.
Only sanitized metadata and fixed error categories are printed; response bodies,
SDK values, management tokens and authorization headers are never printed.
An inaccessible/failed page, missing or masked SDK value, repeated item, changing
SDK total, or safety limit makes the scan INCOMPLETE, even if matches were found.
Previously discovered resources are still scanned after a later page fails.

Exit codes: 0 = match(es), accessible scan complete; 1 = no match, accessible
scan complete; 2 = incomplete full scan (matches may exist), or an inconclusive
lookup when -IdentityOnly is used;
3 = configuration/setup error; 4 = identity metadata only, full scan not performed;
5 = matches found and credential scan complete, but extra details/history incomplete.
The authorized owner should use Organization settings > Security > SDK keys,
select the reported project/environment, and locate the reported credential.
Use the organization's incident/revocation process; discovery is not revocation.

API references verified 2026-09-11:
https://launchdarkly.com/docs/api/projects/get-projects
https://launchdarkly.com/docs/api/environments/get-environments-by-project
https://launchdarkly.com/docs/api/sdk-keys-beta/get-sdk-keys
https://launchdarkly.com/docs/home/account/environment/keys
https://launchdarkly.com/docs/api/other/get-caller-identity
https://launchdarkly.com/docs/api/sdk-keys-beta/get-sdk-key-by-key
https://launchdarkly.com/docs/api/account-members/get-member
https://launchdarkly.com/docs/api/audit-log/get-audit-log-entries
https://launchdarkly.com/docs/home/account/roles/role-resources
All three collections support limit/offset. SDK keys require LD-API-Version:
beta and expose items[].value separately from items[].key, name and isDefault.
SDK queries filter kind:sdk without an active filter (include expired keys).
Collection offsets are built locally on the fixed origin. Audit continuation
links must keep the exact host, path, resource filter and bounds; only validated
numeric cursors are used to reconstruct the next URL. Projects/environments
continue until an empty page, even
after short pages. SDK pages use the documented totalCount. Duplicate keys and
page limits prevent loops when a server ignores offset. No legacy apiKey fallback
can establish the name/resource key of every additional SDK credential.
.PARAMETER IdentityOnly
Only request caller identity using the configured exposed SDK value. Never prompt
for a management token or enumerate resources. Exit 4 means metadata was returned,
not a complete scan or a confirmed SDK credential record match.
#>
param([switch]$IdentityOnly)

# ----------------------- OPERATOR CONFIGURATION -----------------------
$KnownSdkSecret = 'REPLACE_WITH_EXPOSED_SDK_SECRET'
$ManagementTokenEnvironmentVariable = 'LD_ACCESS_TOKEN'
$RequestTimeoutSeconds = 30
$MaxRetries = 4                 # Per GET, in addition to the initial attempt.
$MaxRetryDelaySeconds = 60      # Larger server Retry-After => incomplete; no early retry.
$MaxPagesPerCollection = 10000
$MaxAuditPages = 100            # 20 events/page per matched credential; bounded retained history.
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
$script:DetailsIncomplete = $false

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
    # Preserve literal SDK resource path segments, not credential values.
    $Text = [regex]::Replace($Text, '(?i)(sdk|mob|api)-(?!keys?/)[^\s"''<>]*', '[REDACTED]')
    return [regex]::Replace($Text, '[\x00-\x1f\x7f-\x9f]', ' ')
}

function Invoke-LdGet([string]$PathAndQuery, [switch]$CallerIdentity) {
    # Only locally generated paths reach here; no response link can change host.
    if (($CallerIdentity -and $PathAndQuery -cne '/api/v2/caller-identity') -or
        (-not $CallerIdentity -and $PathAndQuery -cnotmatch '^/api/v2/(projects(?:/|\?)|members/[A-Za-z0-9_-]+$|auditlog\?)')) {
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
            if ($CallerIdentity) {
                $request.Headers.Add('Authorization', $KnownSdkSecret)
            }
            else {
                $request.Headers.Add('Authorization', $ManagementToken)
                if ($PathAndQuery.Contains('/sdk-keys')) { $request.Headers.Add('LD-API-Version', 'beta') }
            }
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

function Write-DetailField([string]$Label, $Object, [string]$Field, [switch]$UnixMilliseconds, [switch]$Required) {
    $v = $null
    if ($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Field]) { $v = $Object.PSObject.Properties[$Field].Value }
    if ($null -eq $v) {
        Write-Output ('  {0}: not returned' -f $Label)
        if ($Required) { Write-DetailFailure $Label 'required-field-not-returned' }
        return
    }
    if ($UnixMilliseconds -and $v -isnot [int] -and $v -isnot [long]) {
        Write-DetailFailure $Label 'invalid-timestamp-shape'; return
    }
    if ($v -is [array]) {
        if (@($v | Where-Object { $_ -isnot [string] }).Count -gt 0) {
            Write-DetailFailure $Label 'unsupported-value-shape'; return
        }
        $display = if ($v.Count -eq 0) { '(empty list)' } else { $v -join ', ' }
    }
    elseif ($v -is [string] -or $v -is [bool] -or $v -is [int] -or $v -is [long]) { $display = [string]$v }
    else { Write-DetailFailure $Label 'unsupported-value-shape'; return }
    if ($UnixMilliseconds -and ($v -is [long] -or $v -is [int]) -and $v -gt 0) {
        try { $display += ' | UTC (Unix ms): ' + [DateTimeOffset]::FromUnixTimeMilliseconds($v).ToString('o') }
        catch { $display += ' | UTC conversion unavailable'; Write-DetailFailure $Label 'timestamp-out-of-range' }
    }
    Write-Output ('  {0}: {1}' -f $Label, (Protect-Text $display))
}

function Write-DetailFailure([string]$Scope, [string]$Reason) {
    $script:DetailsIncomplete = $true
    Write-Output ('DETAILS INCOMPLETE: {0} [{1}]' -f (Protect-Text $Scope), $Reason)
}

function Get-NextAuditPath($Next, [string]$Spec, [long]$Before) {
    # Do not forward the token to response-supplied hosts or arbitrary URLs.
    # Accept only documented numeric cursors and the unchanged resource filter,
    # then reconstruct the URL locally. Never print a rejected link.
    try {
        $href = Get-Field $Next 'href'
        if ($href -isnot [string] -or [string]::IsNullOrWhiteSpace($href)) { return $null }
        $uri = [uri]::new([uri]$ApiOrigin, $href)
        if ($uri.Scheme -cne 'https' -or $uri.Host -cne 'app.launchdarkly.com' -or $uri.Port -ne 443 -or
            $uri.UserInfo -or $uri.Fragment -or $uri.AbsolutePath -cne '/api/v2/auditlog') { return $null }
        $query = @{}
        foreach ($pair in $uri.Query.TrimStart('?').Split('&')) {
            $parts = $pair.Split([char[]]'=', 2, [StringSplitOptions]::None)
            if ($parts.Count -ne 2) { return $null }
            $key = [Net.WebUtility]::UrlDecode($parts[0])
            $v = [Net.WebUtility]::UrlDecode($parts[1])
            if ($key -cnotin @('before', 'after', 'limit', 'spec') -or $query.ContainsKey($key)) { return $null }
            $query[$key] = $v
        }
        if ($query['spec'] -cne $Spec -or $query['before'] -notmatch '^\d{1,15}$' -or
            $query['after'] -ne '0' -or $query['limit'] -ne '20') { return $null }
        $cursor = [long]$query['before']
        if ($cursor -le 0 -or $cursor -ge $Before) { return $null }
        return @{ Path = ('/api/v2/auditlog?limit=20&after=0&before=' + $cursor + '&spec=' + [uri]::EscapeDataString($Spec)); Before = $cursor }
    }
    catch { return $null }
}

function Write-CredentialAudit([string]$ProjectKey, [string]$EnvironmentKey, [string]$CredentialKey) {
    $scope = 'audit ' + $ProjectKey + '/' + $EnvironmentKey + '/' + $CredentialKey
    foreach ($part in @($ProjectKey, $EnvironmentKey, $CredentialKey)) {
        if ($part -cnotmatch '^[A-Za-z0-9_.-]+$' -or $part -in @('.', '..')) {
            Write-DetailFailure $scope 'unsupported-resource-specifier'; return
        }
    }
    $spec = 'proj/' + $ProjectKey + ':env/' + $EnvironmentKey + ':sdk-key/' + $CredentialKey
    $before = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    Write-Output ('  Audit resource: ' + (Protect-Text $spec))
    Write-Output ('  Audit window: after Unix ms 0, before {0}; retained/accessible records only, maximum {1} pages.' -f $before, $MaxAuditPages)
    $path = '/api/v2/auditlog?limit=20&after=0&before=' + $before + '&spec=' + [uri]::EscapeDataString($spec)
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $count = 0
    for ($page = 0; $page -lt $MaxAuditPages; $page++) {
        $response = Invoke-LdGet $path
        if (-not $response.Ok) { Write-DetailFailure $scope $response.Reason; return }
        $data = $response.Data
        if ($null -eq $data -or $null -eq $data.PSObject.Properties['items'] -or
            $data.PSObject.Properties['items'].Value -isnot [array]) {
            Write-DetailFailure $scope 'invalid-items'; return
        }
        $entries = @($data.PSObject.Properties['items'].Value)
        foreach ($entry in $entries) {
            $id = Get-Field $entry '_id'
            if ($id -isnot [string] -or [string]::IsNullOrWhiteSpace($id) -or -not $seen.Add($id)) {
                Write-DetailFailure $scope 'missing-id-or-repeated-event'; return
            }
            $count++
            Write-Output ('  Audit event {0} (API resource-filtered management change):' -f $count)
            foreach ($field in @('_id', 'kind', 'name', 'titleVerb')) {
                Write-DetailField ('Audit.' + $field) $entry $field
            }
            Write-DetailField 'Audit.date' $entry 'date' -UnixMilliseconds -Required
            $actor = Get-Field $entry 'member'
            foreach ($field in @('_id', 'firstName', 'lastName', 'email')) {
                Write-DetailField ('Audit.actor.' + $field) $actor $field
            }
            $auditToken = Get-Field $entry 'token'
            foreach ($field in @('_id', 'name', 'serviceToken')) {
                Write-DetailField ('Audit.managementToken.' + $field) $auditToken $field
            }
            foreach ($field in @('_id', 'name', 'maintainerName')) {
                Write-DetailField ('Audit.app.' + $field) (Get-Field $entry 'app') $field
            }
            foreach ($access in @(Get-Field $entry 'accesses')) {
                if ((Get-Field $access 'resource') -ceq $spec) { Write-DetailField 'Audit.action' $access 'action' }
            }
        }
        $links = Get-Field $data '_links'
        if ($null -eq $links -or $links -isnot [pscustomobject]) { Write-DetailFailure $scope 'missing-or-invalid-pagination-links'; return }
        $next = Get-Field $links 'next'
        if ($null -eq $next) {
            if ($null -ne $links.PSObject.Properties['next']) { Write-DetailFailure $scope 'invalid-next-link'; return }
            Write-Output ('  Audit pagination exhausted: {0} events returned within retention/access scope. This is not SDK request/usage history or proof of complete historical coverage.' -f $count)
            return
        }
        $nextPage = Get-NextAuditPath $next $spec $before
        if ($null -eq $nextPage -or $entries.Count -eq 0) { Write-DetailFailure $scope 'unsafe-or-nonprogressing-pagination'; return }
        $path = $nextPage.Path
        $before = $nextPage.Before
    }
    Write-DetailFailure $scope 'audit-page-limit'
}

function Write-CredentialDetails($Credential, $Project, $Environment) {
    Write-Output '  Additional metadata (matched listing snapshot; missing is not a negative finding):'
    foreach ($field in @('description', '_createdByMemberId')) {
        Write-DetailField ('Credential.' + $field) $Credential $field
    }
    Write-DetailField 'Credential._version' $Credential '_version' -Required
    foreach ($field in @('_createdAt', '_updatedAt')) {
        Write-DetailField ('Credential.' + $field) $Credential $field -UnixMilliseconds -Required
    }
    Write-DetailField 'Credential.expiry' $Credential 'expiry' -UnixMilliseconds
    Write-Output '  SDK timestamps retain raw API values; UTC interpretation assumes Unix milliseconds. Null/zero expiry is not proof of non-expiration or revocation.'
    foreach ($field in @('_id', 'tags')) {
        Write-DetailField ('Project.' + $field) $Project $field
    }
    foreach ($field in @('_id', 'tags', 'critical', 'color', 'defaultTtl', 'secureMode', 'defaultTrackEvents', 'requireComments', 'confirmChanges')) {
        Write-DetailField ('Environment.' + $field) $Environment $field
    }
    $creatorId = Get-Field $Credential '_createdByMemberId'
    if ($creatorId -is [string] -and $creatorId -cmatch '^[A-Za-z0-9_-]+$') {
        $member = Invoke-LdGet ('/api/v2/members/' + [uri]::EscapeDataString($creatorId))
        if (-not $member.Ok) { Write-DetailFailure 'creator lookup' $member.Reason }
        elseif ((Get-Field $member.Data '_id') -cne $creatorId) { Write-DetailFailure 'creator lookup' 'identity-mismatch' }
        else {
            foreach ($field in @('_id', 'firstName', 'lastName', 'email', 'role', 'customRoles', '_pendingInvite', '_verified')) {
                Write-DetailField ('Creator.' + $field) $member.Data $field
            }
            Write-Output '  Creator is the historical creator, not necessarily the current operator/owner. Member role is not the SDK key permission set.'
        }
    }
    else { Write-DetailFailure 'creator lookup' 'creator-id-not-returned-or-invalid' }
    Write-CredentialAudit $Project.key $Environment.key $Credential.key
    Write-Output '  Unavailable from these endpoints: per-key last use, source IPs, deployment/repository inventory, SDK request logs, compromise proof, and complete effective flag/view payload scope.'
}

$exitCode = 3
$oldTls = [Net.ServicePointManager]::SecurityProtocol
try {
    if (-not (Test-ReadableSdkValue $KnownSdkSecret) -or
        $RequestTimeoutSeconds -lt 1 -or $RequestTimeoutSeconds -gt 300 -or
        $MaxRetries -lt 0 -or $MaxRetries -gt 10 -or
        $MaxRetryDelaySeconds -lt 1 -or $MaxRetryDelaySeconds -gt 60 -or
        $MaxPagesPerCollection -lt 1 -or $MaxPagesPerCollection -gt 10000 -or
        $MaxAuditPages -lt 1 -or $MaxAuditPages -gt 1000) {
        Write-Output 'CONFIGURATION ERROR: set the exposed SDK secret and valid bounded configuration.'
        exit 3
    }
    $ManagementToken = [Environment]::GetEnvironmentVariable($ManagementTokenEnvironmentVariable)
    Add-Type -AssemblyName System.Net.Http
    [Net.ServicePointManager]::SecurityProtocol = $oldTls -bor [Net.SecurityProtocolType]::Tls12
    $Handler = [System.Net.Http.HttpClientHandler]::new()
    $Handler.AllowAutoRedirect = $false
    $Handler.UseCookies = $false
    $Client = [System.Net.Http.HttpClient]::new($Handler)
    $Client.Timeout = [TimeSpan]::FromSeconds($RequestTimeoutSeconds)
    $Client.MaxResponseContentBufferSize = 16MB
    $exitCode = 2
    Write-Output 'Looking up exposed SDK credential identity (US commercial API); GET only.'
    $identity = Invoke-LdGet '/api/v2/caller-identity' -CallerIdentity
    $identityFieldCount = 0
    if ($identity.Ok) {
        # Ignore every other property, including any unexpected credential values.
        foreach ($field in @('accountId', 'projectId', 'projectName', 'environmentId',
                'environmentName', 'authKind', 'tokenKind', 'tokenName', 'tokenId', 'memberId', 'clientId')) {
            $fieldValue = Get-Field $identity.Data $field
            if ($fieldValue -is [string] -and -not [string]::IsNullOrWhiteSpace($fieldValue)) {
                Write-Output ('  Identity {0}: {1}' -f $field, (Protect-Text $fieldValue))
                $identityFieldCount++
            }
        }
        Write-DetailField 'Identity.serviceToken' $identity.Data 'serviceToken'
        Write-DetailField 'Identity.scopes' $identity.Data 'scopes'
        if ((Get-Field $identity.Data 'serviceToken') -is [bool]) { $identityFieldCount++ }
        $scopeProperty = if ($null -ne $identity.Data) { $identity.Data.PSObject.Properties['scopes'] } else { $null }
        if ($null -ne $scopeProperty -and $scopeProperty.Value -is [array] -and
            @($scopeProperty.Value | Where-Object { $_ -isnot [string] }).Count -eq 0) { $identityFieldCount++ }
        if ($identityFieldCount -eq 0) {
            Write-Output 'IDENTITY INCONCLUSIVE: no recognized metadata returned.'
        }
        else {
            Write-Output 'IDENTITY METADATA: optional fields only; not a confirmed SDK credential record match or full scan.'
        }
    }
    else {
        Write-Output ('IDENTITY INCONCLUSIVE: [' + $identity.Reason + ']. Failure does not identify the owner or prove revocation.')
    }
    $identity = $null
    if ($IdentityOnly) {
        if ($identityFieldCount -gt 0) { exit 4 }
        exit 2
    }
    Write-Output 'Continuing full discovery using a separate management token; identity lookup status does not determine scan completeness.'
    $exitCode = 3 # A missing/unreadable management token is still a setup failure.
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
                    Write-CredentialDetails $credential $project $environment
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
    elseif ($script:DetailsIncomplete) {
        Write-Output 'FOUND: credential scan complete, but supplementary details/history are incomplete. Preserve the confirmed matches and reported detail failures.'
        $exitCode = 5
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
