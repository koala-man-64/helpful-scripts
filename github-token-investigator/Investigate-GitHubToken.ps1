#requires -Version 7.2
[CmdletBinding()]
param(
    [string]$ApiBaseUrl = 'https://api.github.com',
    [switch]$AllowEnterpriseHost,
    [Security.SecureString]$Token,
    [string]$Repository,
    [switch]$NonInteractive
)

# Dot-source for offline tests. Only the entry point reads credentials or opens HTTP.
function Get-GhField($Object, [string]$Name) {
    if ($Object -is [Collections.IDictionary]) { return ,($Object[$Name]) }
    return $null
}

function Get-GhSafeText($Value, [string]$Secret) {
    if ($Value -isnot [string]) { return $null }
    $text = $Value
    if ($Secret) { $text = $text.Replace($Secret, '[REDACTED]').Replace([uri]::EscapeDataString($Secret), '[REDACTED]') }
    $text = $text -replace '(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)', '[REDACTED]'
    $text = $text -replace '[\p{Cc}\p{Cf}]', ' '
    if ($text.Length -gt 256) { $text = $text.Substring(0, 256) + '...' }
    return $text
}

function Get-GhBase([string]$Value) {
    $uri = $null
    if ($Value -cnotmatch '^https://[^/?#\\\s]+(?:/api/v3)?/?$' -or
        -not [uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -or
        $uri.UserInfo -or -not $uri.Host -or $uri.HostNameType -eq [UriHostNameType]::Unknown) { return $null }
    $base = $Value.TrimEnd('/')
    if ($base -cne ($uri.GetLeftPart([UriPartial]::Authority) + $uri.AbsolutePath.TrimEnd('/'))) { return $null }
    return $base
}

function Test-GhRepository([string]$Value) {
    return $Value -cmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}$' -and
        ($Value.Split('/')[1] -notin @('.', '..'))
}

function New-GhHandler {
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseCookies = $false
    return $handler
}

function New-GhClient([string]$Secret) {
    $client = [Net.Http.HttpClient]::new((New-GhHandler))
    try {
        $client.Timeout = [TimeSpan]::FromSeconds(20)
        $client.MaxResponseContentBufferSize = 1048576
        $client.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $Secret)
        $client.DefaultRequestHeaders.Accept.ParseAdd('application/vnd.github+json')
        $client.DefaultRequestHeaders.UserAgent.ParseAdd('github-token-investigator/1.0')
        $client.DefaultRequestHeaders.Add('X-GitHub-Api-Version', '2022-11-28')
        return $client
    }
    catch { $client.Dispose(); throw }
}

function Invoke-GhGet($Client, [string]$Base, [string]$Path) {
    if (-not (Get-GhBase $Base) -or ($Path -cne '/user' -and
        (-not $Path.StartsWith('/repos/') -or -not (Test-GhRepository $Path.Substring(7))))) {
        return @{status='unsafe_endpoint'; httpStatus=$null; headers=@{}; data=$null}
    }
    $response = $null
    try {
        # No automatic retries: avoid extra credential use and respect secondary rate limits.
        $response = $Client.GetAsync($Base + $Path).GetAwaiter().GetResult()
        $status = [int]$response.StatusCode
        $headers = @{}
        foreach ($name in @('X-OAuth-Scopes', 'GitHub-Authentication-Token-Expiration', 'X-GitHub-SSO', 'X-RateLimit-Remaining', 'X-RateLimit-Reset', 'Retry-After')) {
            if ($response.Headers.Contains($name)) { $headers[$name] = [string]::Join(', ', $response.Headers.GetValues($name)) }
        }
        if ($status -ne 200) { return @{status="http_$status"; httpStatus=$status; headers=$headers; data=$null} }
        if ($response.Content.Headers.ContentType.MediaType -notin @('application/json', 'application/vnd.github+json')) {
            return @{status='unexpected_content_type'; httpStatus=200; headers=$headers; data=$null}
        }
        try { $data = ConvertFrom-Json ($response.Content.ReadAsStringAsync().GetAwaiter().GetResult()) -AsHashtable -Depth 30 -ErrorAction Stop }
        catch { return @{status='invalid_json'; httpStatus=200; headers=$headers; data=$null} }
        if ($data -isnot [Collections.IDictionary]) { return @{status='unexpected_document'; httpStatus=200; headers=$headers; data=$null} }
        return @{status='ok'; httpStatus=200; headers=$headers; data=$data}
    }
    catch { return @{status='transport_error'; httpStatus=$null; headers=@{}; data=$null} }
    finally { if ($null -ne $response) { $response.Dispose() } }
}

function Get-GhEvidence($Reply, [string]$Secret) {
    $sso = Get-GhField $Reply.headers 'X-GitHub-SSO'
    $ssoState = if ($sso -is [string] -and $sso -cmatch '^(required|partial-results)(?:;|$)') { $Matches[1] } else { 'not_reported' }
    return [ordered]@{
        status=$Reply.status; httpStatus=$Reply.httpStatus; sso=$ssoState
        rateLimitRemaining=(Get-GhSafeText (Get-GhField $Reply.headers 'X-RateLimit-Remaining') $Secret)
        rateLimitReset=(Get-GhSafeText (Get-GhField $Reply.headers 'X-RateLimit-Reset') $Secret)
        retryAfter=(Get-GhSafeText (Get-GhField $Reply.headers 'Retry-After') $Secret)
    }
}

function Invoke-GhInvestigation([string]$Secret, [string]$Repo, [scriptblock]$Get) {
    $kind = if ($Secret.StartsWith('github_pat_')) { 'fine_grained_pat_prefix' } elseif ($Secret.StartsWith('ghp_')) { 'classic_pat_prefix' } else { 'unknown' }
    $result = [ordered]@{
        status='unresolved'; observedAtUtc=[DateTimeOffset]::UtcNow.ToString('o'); owner=$null
        credential=[ordered]@{kindHint=$kind; scopes=$null; scopesEvidence='not_reported'; expiration=$null; exactTokenId=$null; name=$null; createdAt=$null; lastUsedAt=$null}
        identityEvidence=$null; repository=$null
        limitations=@('Prefix is a format hint, not verified token type.', 'Identity is the authenticated account, not the exact token record or creator.', 'No token creation time, last use, complete permissions or historical usage can be inferred from these requests.', 'This probe itself uses the credential and may affect audit or last-used records.', 'Missing scope or expiration headers mean unknown, not unrestricted access or no expiry.')
        nextSteps='Have the account owner or authorized administrator inspect token settings and retained security/audit records; revoke or rotate through the approved process if needed.'
    }
    $identity = & $Get '/user'
    $result.identityEvidence = Get-GhEvidence $identity $Secret
    if ($identity.status -ne 'ok') { return $result }
    $login = Get-GhField $identity.data 'login'
    $id = Get-GhField $identity.data 'id'
    if ($login -isnot [string] -or $login -cnotmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,38}$' -or
        ($id -isnot [long] -and $id -isnot [int] -and $id -isnot [bigint]) -or $id -le 0) {
        $result.identityEvidence.status = 'unexpected_identity'
        return $result
    }
    $result.owner = [ordered]@{login=(Get-GhSafeText $login $Secret); id=(Get-GhSafeText ([string]$id) $Secret); accountType=(Get-GhSafeText (Get-GhField $identity.data 'type') $Secret); name=(Get-GhSafeText (Get-GhField $identity.data 'name') $Secret)}
    $result.status = 'identified'
    $scopes = Get-GhField $identity.headers 'X-OAuth-Scopes'
    if ($scopes -is [string]) {
        $result.credential.scopes = Get-GhSafeText $scopes $Secret
        $result.credential.scopesEvidence = 'response_header_not_complete_effective_permissions'
    }
    $result.credential.expiration = Get-GhSafeText (Get-GhField $identity.headers 'GitHub-Authentication-Token-Expiration') $Secret
    if ($Repo) {
        $reply = & $Get ('/repos/' + $Repo)
        $result.repository = [ordered]@{requested=(Get-GhSafeText $Repo $Secret); metadataReadable=$false; evidence=(Get-GhEvidence $reply $Secret); limitation='A successful metadata GET, including for a public repository, does not prove private content, write or admin access. A 404 may mean hidden, absent, or inaccessible.'}
        if ($reply.status -eq 'ok' -and (Get-GhField $reply.data 'full_name') -is [string] -and
            (Get-GhField $reply.data 'full_name') -ieq $Repo) { $result.repository.metadataReadable = $true }
        else {
            $result.status = 'partial'
            if ($reply.status -eq 'ok') { $result.repository.evidence.status = 'resource_mismatch' }
        }
    }
    return $result
}

function Start-GhInvestigation {
    $client = $null; $plain = $null; $pointer = [IntPtr]::Zero; $prompted = $null
    try {
        $base = Get-GhBase $ApiBaseUrl
        if ($base -and $base -cne 'https://api.github.com' -and -not $AllowEnterpriseHost) {
            return @{status='configuration_error'; reason='Custom API destinations require -AllowEnterpriseHost. Verify the trusted GitHub Enterprise API URL before opting in.'}
        }
        if (-not $base -or ($Repository -and -not (Test-GhRepository $Repository))) {
            return @{status='configuration_error'; reason='Use a trusted HTTPS API base with no credentials, query or fragment; only an optional /api/v3 path. Repository must be owner/name.'}
        }
        $inputToken = $Token
        if ($null -eq $inputToken) {
            $plain = [Environment]::GetEnvironmentVariable('GITHUB_INVESTIGATION_TOKEN', 'Process')
            if (-not $plain -and -not $NonInteractive) {
                $prompted = Read-Host 'GitHub personal access credential (hidden)' -AsSecureString
                $inputToken = $prompted
            }
        }
        if ($null -ne $inputToken) {
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($inputToken)
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        }
        if (-not $plain -or $plain -cmatch '[^\x21-\x7e]' -or $plain.Length -gt 16384) {
            return @{status='configuration_error'; reason='Provide a nonempty credential without whitespace through the secure prompt, SecureString or documented environment variable.'}
        }
        $client = New-GhClient $plain
        $get = { param($path) Invoke-GhGet $client $base $path }.GetNewClosure()
        return Invoke-GhInvestigation $plain $Repository $get
    }
    catch { return @{status='internal_error'; reason='Investigation stopped safely. Exception details are suppressed.'} }
    finally {
        if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        if ($null -ne $prompted) { $prompted.Dispose() }
        if ($null -ne $client) { $client.Dispose() }
        $plain = $null
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $outcome = Start-GhInvestigation
    $outcome | ConvertTo-Json -Depth 8 -Compress
    switch ($outcome.status) {
        'identified' { exit 0 }
        'partial' { exit 2 }
        'unresolved' { exit 3 }
        default { exit 4 }
    }
}
