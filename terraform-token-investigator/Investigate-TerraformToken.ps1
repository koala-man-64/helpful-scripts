#requires -Version 7.2
[CmdletBinding()]
param(
    [string]$Origin = $env:TERRAFORM_INVESTIGATION_ORIGIN,
    [Security.SecureString]$Token,
    [switch]$NonInteractive
)

# Dot-source for offline tests. Only the entry point reads credentials or opens HTTP.
function Get-Field($Object, [string]$Name) {
    if ($Object -is [System.Collections.IDictionary]) { return $Object[$Name] }
    return $null
}

function Get-SafeText($Value, [string]$Secret, [string[]]$AdditionalSecrets = @()) {
    if ($Value -isnot [string]) { return $null }
    $text = $Value
    foreach ($credential in (@($Secret) + $AdditionalSecrets)) {
        if ($credential) {
            $text = $text.Replace($credential, '[REDACTED]').Replace([uri]::EscapeDataString($credential), '[REDACTED]')
        }
    }
    $text = $text -replace '[A-Za-z0-9_-]+\.atlasv1\.[A-Za-z0-9_-]+', '[REDACTED]'
    $text = $text -replace '[\p{Cc}\p{Cf}]', ' '
    if ($text.Length -gt 256) { $text = $text.Substring(0, 256) + '...' }
    return $text
}

function Get-ApprovedOrigin([string]$Value) {
    $uri = $null
    if ($Value -cnotmatch '^https://[^/?#\\\s]+/?$' -or
        -not [uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -ne 'https' -or $uri.UserInfo -or -not $uri.Host -or
        $uri.HostNameType -eq [UriHostNameType]::Unknown) { return $null }
    return $uri.GetLeftPart([UriPartial]::Authority)
}

function Get-ApprovedPath($Link, [string]$BaseOrigin, [string]$Pattern) {
    if ($Link -isnot [string] -or $Link -match '[\\\s%?#]' -or $Link.StartsWith('//')) { return $null }
    $path = $Link
    if (-not $Link.StartsWith('/')) {
        $uri = $null
        if (-not [uri]::TryCreate($Link, [UriKind]::Absolute, [ref]$uri) -or
            $uri.Scheme -ne 'https' -or $uri.UserInfo -or
            $uri.GetLeftPart([UriPartial]::Authority) -ine $BaseOrigin) { return $null }
        $path = $uri.AbsolutePath
        # Reject normalized dot segments and any noncanonical URL spelling.
        if ($Link -cne ($uri.GetLeftPart([UriPartial]::Authority) + $path)) { return $null }
    }
    if ($path -cnotmatch $Pattern) { return $null }
    return $path
}

function Invoke-TerraformGet($Client, [string]$BaseOrigin, [string]$Path) {
    # Internal transport: paths have already passed an endpoint-specific allowlist.
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $response = $null
        try {
            $response = $Client.GetAsync($BaseOrigin + $Path).GetAwaiter().GetResult()
            $status = [int]$response.StatusCode
            if ($status -in @(429, 502, 503, 504) -and $attempt -lt 2) {
                $delay = [double][math]::Pow(2, $attempt)
                $retry = $response.Headers.RetryAfter
                if ($null -ne $retry) {
                    if ($null -ne $retry.Delta) { $delay = $retry.Delta.TotalSeconds }
                    elseif ($null -ne $retry.Date) { $delay = ($retry.Date - [DateTimeOffset]::UtcNow).TotalSeconds }
                }
                # Do not retry earlier than requested if the server asks for a long wait.
                if ($delay -le 10) {
                    $response.Dispose(); $response = $null
                    Start-Sleep -Milliseconds ([int]([math]::Max(0, $delay) * 1000))
                    continue
                }
            }
            if ($status -ne 200) { return @{ status = "http_$status"; httpStatus = $status; data = $null } }
            $media = $response.Content.Headers.ContentType.MediaType
            if ($media -notin @('application/vnd.api+json', 'application/json')) {
                return @{ status = 'unexpected_content_type'; httpStatus = 200; data = $null }
            }
            $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            try { $document = ConvertFrom-Json -InputObject $body -AsHashtable -Depth 30 -ErrorAction Stop }
            catch { return @{ status = 'invalid_json'; httpStatus = 200; data = $null } }
            $data = Get-Field $document 'data'
            if ($data -isnot [System.Collections.IDictionary]) {
                return @{ status = 'unexpected_document'; httpStatus = 200; data = $null }
            }
            return @{ status = 'ok'; httpStatus = 200; data = $data }
        }
        catch { return @{ status = 'transport_error'; httpStatus = $null; data = $null } }
        finally { if ($null -ne $response) { $response.Dispose() } }
    }
}

function Invoke-TerraformInvestigation([string]$BaseOrigin, [string]$Secret, [scriptblock]$Get) {
    $result = [ordered]@{
        host = $BaseOrigin; status = 'unresolved'
        owner = [ordered]@{ type = $null; id = $null; name = $null; evidence = 'unavailable' }
        token = [ordered]@{ id = $null; resourcePath = $null; description = $null; evidence = 'unavailable' }
        evidence = [System.Collections.Generic.List[object]]::new()
        nextSteps = @('Give these findings to the authorized administrator. Identification does not revoke a credential. Do not share the secret.')
    }
    function Add-Evidence([string]$Step, $Reply) {
        $result.evidence.Add([ordered]@{ step = $Step; status = $Reply.status; httpStatus = $Reply.httpStatus })
    }
    $account = & $Get '/api/v2/account/details'
    Add-Evidence 'account' $account
    if ($account.status -ne 'ok') { return $result }
    $data = $account.data
    if ((Get-Field $data 'type') -isnot [string] -or (Get-Field $data 'type') -cne 'users' -or
        (Get-Field $data 'id') -isnot [string] -or (Get-Field $data 'id') -cnotmatch '^user-[A-Za-z0-9]+$') {
        Add-Evidence 'account_shape' @{status='unexpected_resource'; httpStatus=200}
        return $result
    }
    $result.status = 'partial'
    $attributes = Get-Field $data 'attributes'
    $relationship = Get-Field (Get-Field $data 'relationships') 'authenticated-resource'
    $owner = Get-Field $relationship 'data'
    $ownerType = Get-Field $owner 'type'
    $ownerId = Get-Field $owner 'id'
    $ownerPattern = if ($ownerType -is [string]) { switch -CaseSensitive ($ownerType) {
        'users' { '^user-[A-Za-z0-9]+$' }
        'teams' { '^team-[A-Za-z0-9]+$' }
        'organizations' { '^[A-Za-z0-9][A-Za-z0-9_-]*$' }
        default { $null }
    } } else { $null }
    if ($ownerPattern -and $ownerId -is [string] -and $ownerId -cmatch $ownerPattern) {
        $result.owner.type = $ownerType
        $result.owner.id = Get-SafeText $ownerId $Secret
        $result.owner.evidence = 'authenticated-resource'
    }
    elseif ($null -eq $relationship -and (Get-Field $attributes 'is-service-account') -is [bool] -and
            (Get-Field $attributes 'is-service-account') -eq $false) {
        $ownerType = 'users'; $ownerId = $data.id
        $result.owner.type = $ownerType; $result.owner.id = Get-SafeText $ownerId $Secret
        $result.owner.evidence = 'account_non_service_user'
    }
    else { Add-Evidence 'owner' @{status='missing_or_unsupported_relationship'; httpStatus=$null} }

    if ($result.owner.type) {
        if ($ownerType -eq 'users' -and $ownerId -ceq $data.id) {
            $result.owner.name = Get-SafeText (Get-Field $attributes 'username') $Secret
        }
        else {
            $link = Get-Field (Get-Field $relationship 'links') 'related'
            # Known user/team IDs have documented detail endpoints; organization routes use names.
            if (-not $link -and $ownerType -in @('users','teams')) { $link = "/api/v2/$ownerType/$ownerId" }
            $pattern = '^/api/v2/' + $ownerType + '/[A-Za-z0-9][A-Za-z0-9_-]*$'
            $path = Get-ApprovedPath $link $BaseOrigin $pattern
            if ($path) {
                $detail = & $Get $path
                Add-Evidence 'owner_details' $detail
                if ($detail.status -eq 'ok') {
                    if ((Get-Field $detail.data 'type') -ceq $ownerType -and (Get-Field $detail.data 'id') -ceq $ownerId) {
                        $nameField = if ($ownerType -eq 'users') { 'username' } else { 'name' }
                        $result.owner.name = Get-SafeText (Get-Field (Get-Field $detail.data 'attributes') $nameField) $Secret
                    }
                    else { Add-Evidence 'owner_identity' @{status='resource_mismatch'; httpStatus=200} }
                }
            }
            else { Add-Evidence 'owner_link' @{status='missing_or_unsafe_link'; httpStatus=$null} }
        }
    }
    $link = Get-Field (Get-Field $data 'links') 'auth-token'
    $path = Get-ApprovedPath $link $BaseOrigin '^/api/v2/authentication-tokens/at-[A-Za-z0-9]+$'
    if ($path) {
        $id = $path.Split('/')[-1]
        $result.token.id = Get-SafeText $id $Secret
        $result.token.resourcePath = Get-SafeText $path $Secret
        $result.token.evidence = 'account_auth-token_link'
        # Direct current-resource link; never list credentials or use a legacy singleton as identity proof.
        $metadata = if ($result.owner.type -in @('users','teams')) { & $Get $path }
                    else { @{status='not_attempted_for_owner_type'; httpStatus=$null} }
        Add-Evidence 'token_metadata' $metadata
        if ($metadata.status -eq 'ok') {
            if ((Get-Field $metadata.data 'type') -ceq 'authentication-tokens' -and (Get-Field $metadata.data 'id') -ceq $id) {
                $tokenAttributes = Get-Field $metadata.data 'attributes'
                # Some documentation examples include a token field even on GET.
                # Never emit it, and redact it if repeated in the requested description.
                $returnedSecret = Get-Field $tokenAttributes 'token'
                $extraSecrets = if ($returnedSecret -is [string]) { @($returnedSecret) } else { @() }
                $result.token.description = Get-SafeText (Get-Field $tokenAttributes 'description') $Secret $extraSecrets
                $result.token.evidence = 'account_link_and_matching_metadata'
            }
            else { Add-Evidence 'token_identity' @{status='resource_mismatch'; httpStatus=200} }
        }
    }
    else { Add-Evidence 'token_link' @{status='missing_or_unsafe_link'; httpStatus=$null} }
    if ($result.owner.type -and $result.token.id) { $result.status = 'identified' }
    $handoff = switch ($result.owner.type) {
        'users' { 'Contact the owning user and security administrator to revoke the exact user token ID through the approved process.' }
        'teams' { 'Contact an organization owner or authorized team-token administrator with the team and exact token IDs.' }
        'organizations' { 'Contact an organization owner with the organization and exact token IDs.' }
        default { 'Ask the issuing-instance administrator to resolve ownership using the installed-version documentation and audit records; do not guess from service usernames.' }
    }
    $result.nextSteps += $handoff
    return $result
}

function Start-TerraformInvestigation {
    $client = $null; $handler = $null; $plain = $null; $pointer = [IntPtr]::Zero
    try {
        $approved = Get-ApprovedOrigin $Origin
        if (-not $approved) { return @{status='configuration_error'; reason='Set an explicit HTTPS origin with no credentials, path, query or fragment.'} }
        if ($PSVersionTable.PSVersion -lt [version]'7.2') { return @{status='configuration_error'; reason='PowerShell 7.2 or newer is required.'} }
        if ($null -ne $Token) {
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Token)
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        }
        else { $plain = [Environment]::GetEnvironmentVariable('TERRAFORM_INVESTIGATION_TOKEN', 'Process') }
        if (-not $plain -and -not $NonInteractive) {
            $prompted = Read-Host 'Terraform API credential (hidden)' -AsSecureString
            try {
                $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($prompted)
                $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
            }
            finally { $prompted.Dispose() }
        }
        if (-not $plain -or $plain -match '\s' -or $plain.Length -gt 16384) {
            return @{status='configuration_error'; reason='Provide a nonempty credential without whitespace via secure prompt or the documented environment variable.'}
        }
        $handler = [Net.Http.HttpClientHandler]::new()
        $handler.AllowAutoRedirect = $false
        $handler.UseCookies = $false
        $client = [Net.Http.HttpClient]::new($handler)
        $client.Timeout = [TimeSpan]::FromSeconds(20)
        $client.MaxResponseContentBufferSize = 1048576
        $client.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $plain)
        $client.DefaultRequestHeaders.Accept.ParseAdd('application/vnd.api+json')
        $get = { param($path) Invoke-TerraformGet $client $approved $path }.GetNewClosure()
        return Invoke-TerraformInvestigation $approved $plain $get
    }
    catch { return @{status='internal_error'; reason='Investigation stopped safely. No exception details are emitted.'} }
    finally {
        if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        if ($null -ne $client) { $client.Dispose() }
        if ($null -ne $handler) { $handler.Dispose() }
        $plain = $null
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $outcome = Start-TerraformInvestigation
    $outcome | ConvertTo-Json -Depth 8 -Compress
    switch ($outcome.status) {
        'identified' { exit 0 }
        'partial' { exit 2 }
        'unresolved' { exit 3 }
        default { exit 4 }
    }
}
