#requires -Version 7.2
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/Investigate-GitHubToken.ps1"
$script:assertions = 0
function Assert($Condition, [string]$Message) {
    if (-not $Condition) { throw "FAILED: $Message" }
    $script:assertions++
}
function New-Identity {
    return @{status='ok'; httpStatus=200; headers=@{}; data=@{login='octocat'; id=1; name='Example'}}
}
function Run-Case($Identity = (New-Identity), $RepoReply = $null, [string]$Repo = '', [string]$Credential = 'FAKE_FIXTURE_ONLY') {
    $script:paths = [Collections.Generic.List[string]]::new()
    $calls = $script:paths
    $get = { param($path) $calls.Add($path); if ($path -eq '/user') { return $Identity }; return $RepoReply }.GetNewClosure()
    return Invoke-GhInvestigation $Credential $Repo $get
}
$r = Run-Case
Assert ($r.status -eq 'identified' -and $r.owner.login -eq 'octocat') 'account identified'
Assert ($script:paths.Count -eq 1 -and $null -eq $r.credential.exactTokenId) 'one call and no invented record'
Assert ($null -eq $r.credential.scopes -and $null -eq $r.credential.expiration) 'missing metadata stays unknown'
$i = New-Identity; $i.headers = @{'X-OAuth-Scopes'=''; 'GitHub-Authentication-Token-Expiration'='2027-01-01 00:00:00 UTC'}
$r = Run-Case $i
Assert ($r.credential.scopes -ceq '' -and $r.credential.scopesEvidence -ne 'not_reported') 'empty scopes distinct from missing'
Assert ($r.credential.expiration -eq '2027-01-01 00:00:00 UTC') 'expiration header selected'
foreach ($bad in @(@{}, @{login='octocat'; id='1'}, @{login=@('a'); id=1}, @{login='a'; id=0}, @{login='a'; id=1.5})) {
    $i = New-Identity; $i.data = $bad
    Assert ((Run-Case $i).status -eq 'unresolved') 'malformed identity rejected'
}
foreach ($status in @(401,403,404,429,500)) {
    $i = @{status="http_$status"; httpStatus=$status; headers=@{}}
    $r = Run-Case $i $null 'owner/repo'
    Assert ($r.status -eq 'unresolved' -and $null -eq $r.owner -and $script:paths.Count -eq 1) 'failed identity stops probing'
}
$repo = @{status='ok'; httpStatus=200; headers=@{}; data=@{full_name='owner/repo'; permissions=@{admin=$true}}}
$r = Run-Case (New-Identity) $repo 'owner/repo'
Assert ($r.status -eq 'identified' -and $r.repository.metadataReadable -and $script:paths.Count -eq 2) 'named repository GET'
Assert (-not $r.repository.Contains('permissions')) 'no account permission inflation'
$repo.data.full_name='other/repo'
$r = Run-Case (New-Identity) $repo 'owner/repo'
Assert ($r.status -eq 'partial' -and -not $r.repository.metadataReadable -and $r.owner.login -eq 'octocat') 'mismatch preserves account evidence'
$repo = @{status='http_403'; httpStatus=403; headers=@{'X-GitHub-SSO'='required; url=https://example.test/private'; 'Retry-After'='60'}}
$r = Run-Case (New-Identity) $repo 'owner/repo'
Assert ($r.status -eq 'partial' -and $r.repository.evidence.sso -eq 'required') 'SSO classified'
Assert (($r | ConvertTo-Json -Depth 8) -notmatch 'example.test/private') 'SSO link suppressed'
$i = New-Identity; $i.data.name="FAKE_FIXTURE_ONLY`nother ghp_fixture"; $i.headers=@{'X-OAuth-Scopes'='FAKE_FIXTURE_ONLY'}
$r = Run-Case $i
Assert (($r | ConvertTo-Json -Depth 8) -notmatch 'FAKE_FIXTURE_ONLY|ghp_fixture') 'body and headers redacted'
Assert ((Get-GhSafeText ('x' * 300 + 'FAKE_FIXTURE_ONLY') 'FAKE_FIXTURE_ONLY').Length -le 259) 'redact before truncation'
Assert ((Get-GhSafeText 'a%2Fb' 'a/b') -eq '[REDACTED]') 'URL encoded credential redacted'
Assert ((Run-Case -Credential 'github_pat_fixture').credential.kindHint -eq 'fine_grained_pat_prefix') 'fine grained hint'
Assert ((Run-Case -Credential 'ghp_fixture').credential.kindHint -eq 'classic_pat_prefix') 'classic hint'
foreach ($bad in @('http://example.test', 'https://user@example.test', 'https://example.test/api/v3/../x', 'https://example.test/?q=x', 'https://example.test/#x', 'https://example.test\x', 'https://example.test/api%2fv3', 'https://example.test//')) {
    Assert ($null -eq (Get-GhBase $bad)) 'unsafe base rejected'
}
Assert ((Get-GhBase 'https://example.test:8443/api/v3/') -eq 'https://example.test:8443/api/v3') 'enterprise base'
Assert ((Get-GhBase 'https://api.github.com') -eq 'https://api.github.com') 'default base'
foreach ($bad in @('owner/..', 'owner/.', 'owner/repo?x=1', 'owner/repo#x', 'owner/repo/extra', '//evil.test', 'owner/%2e')) {
    Assert (-not (Test-GhRepository $bad)) 'unsafe repository rejected'
}

# In-memory HttpClient handler: no DNS, sockets or real credentials.
Add-Type -TypeDefinition @'
using System;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
public class GitHubFixtureHandler : HttpMessageHandler {
    public HttpResponseMessage Reply;
    public int Calls;
    public bool Fail;
    public bool Delay;
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage req, CancellationToken ct) {
        Calls++;
        if (req.Method != HttpMethod.Get) throw new Exception("non-GET");
        if (Fail) throw new Exception("FAKE_FIXTURE_ONLY private response");
        if (Delay) return Delayed(ct);
        return Task.FromResult(Reply);
    }
    private async Task<HttpResponseMessage> Delayed(CancellationToken ct) {
        await Task.Delay(2000, ct);
        return Reply;
    }
}
'@
function Run-Http([int]$Status=200, [string]$Body='{"login":"octocat","id":1}', [string]$Media='application/json', [switch]$Fail, [switch]$Delay, [string]$Path='/user') {
    $script:handler = [GitHubFixtureHandler]::new()
    $script:handler.Fail=$Fail; $script:handler.Delay=$Delay
    $reply = [Net.Http.HttpResponseMessage]::new([Net.HttpStatusCode]$Status)
    $reply.Content = [Net.Http.StringContent]::new($Body, [Text.Encoding]::UTF8, $Media)
    $reply.Headers.TryAddWithoutValidation('X-OAuth-Scopes', 'repo') | Out-Null
    $reply.Headers.Location = [uri]'https://evil.test/'
    $script:handler.Reply=$reply
    $client=[Net.Http.HttpClient]::new($script:handler)
    $client.MaxResponseContentBufferSize=1048576
    $client.Timeout=[TimeSpan]::FromMilliseconds(100)
    try { return Invoke-GhGet $client 'https://api.github.com' $Path }
    finally { $client.Dispose() }
}
$r = Run-Http
Assert ($r.status -eq 'ok' -and $r.headers['X-OAuth-Scopes'] -eq 'repo') 'real HTTP parsing and headers'
foreach ($status in @(301,302,307,401,403,404,429,500,503)) {
    $r=Run-Http -Status $status -Body 'FAKE_FIXTURE_ONLY'
    Assert ($r.status -eq "http_$status" -and $script:handler.Calls -eq 1 -and $null -eq $r.data) 'HTTP failure bounded and body suppressed'
}
foreach ($body in @('null','[]','"text"')) { Assert ((Run-Http -Body $body).status -eq 'unexpected_document') 'non-object JSON rejected' }
Assert ((Run-Http -Body '{').status -eq 'invalid_json') 'invalid JSON safe'
Assert ((Run-Http -Media 'text/html').status -eq 'unexpected_content_type') 'HTML rejected'
Assert ((Run-Http -Body ('x' * 1048577)).status -eq 'transport_error') 'response size bounded'
Assert ((Run-Http -Delay).status -eq 'transport_error') 'timeout bounded'
$r=Run-Http -Fail
Assert ($r.status -eq 'transport_error' -and ($r | ConvertTo-Json) -notmatch 'FAKE_FIXTURE_ONLY|private response') 'transport exception suppressed'
foreach ($path in @('https://evil.test', '/repos/owner/../user', '/user?x=1', '/users')) {
    $r=Run-Http -Path $path
    Assert ($r.status -eq 'unsafe_endpoint' -and $script:handler.Calls -eq 0) 'transport allowlist'
}
$handler=New-GhHandler
try { Assert (-not $handler.AllowAutoRedirect -and -not $handler.UseCookies) 'production redirects and cookies disabled' }
finally { $handler.Dispose() }
$client=New-GhClient 'FAKE_FIXTURE_ONLY'
try {
    Assert ($client.DefaultRequestHeaders.Authorization.Scheme -eq 'Bearer') 'authorization scheme'
    Assert ($client.Timeout.TotalSeconds -eq 20 -and $client.MaxResponseContentBufferSize -eq 1048576) 'production limits'
    Assert ($client.DefaultRequestHeaders.UserAgent.ToString() -eq 'github-token-investigator/1.0') 'user agent'
}
finally { $client.Dispose() }
$savedTransport=${function:Invoke-GhGet}
$script:entryFixture=[guid]::NewGuid().ToString('N')
function Invoke-GhGet($Client, $Base, $Path) {
    Assert ($Client.DefaultRequestHeaders.Authorization.Parameter -eq $script:entryFixture) 'entry consumes SecureString'
    return New-Identity
}
$Token=ConvertTo-SecureString $script:entryFixture -AsPlainText -Force
try {
    Assert ((Start-GhInvestigation).status -eq 'identified') 'secure entry path'
    $ApiBaseUrl='https://enterprise.example.test/api/v3'
    Assert ((Start-GhInvestigation).status -eq 'configuration_error') 'custom destination requires opt-in'
    $AllowEnterpriseHost=$true
    Assert ((Start-GhInvestigation).status -eq 'identified') 'explicit enterprise destination accepted'
}
finally { $Token.Dispose(); $Token=$null; ${function:Invoke-GhGet}=$savedTransport }
$ApiBaseUrl='http://invalid.test'
Assert ((Start-GhInvestigation).status -eq 'configuration_error') 'configuration rejected before credentials'
$ApiBaseUrl='https://api.github.com'
$invalidFixture=$script:entryFixture + [char]10
$Token=ConvertTo-SecureString $invalidFixture -AsPlainText -Force
try { Assert ((Start-GhInvestigation).status -eq 'configuration_error') 'header injection rejected' }
finally { $Token.Dispose(); $Token=$null }
# A subprocess checks the actual JSON/exit path without inherited investigation credentials.
$savedEnvironment=[Environment]::GetEnvironmentVariable('GITHUB_INVESTIGATION_TOKEN', 'Process')
try {
    [Environment]::SetEnvironmentVariable('GITHUB_INVESTIGATION_TOKEN', $null, 'Process')
    $output=& (Join-Path $PSHOME 'pwsh') -NoProfile -File "$PSScriptRoot/Investigate-GitHubToken.ps1" -NonInteractive
    Assert ($LASTEXITCODE -eq 4 -and ($output | ConvertFrom-Json).status -eq 'configuration_error') 'CLI missing input and exit code'
}
finally { [Environment]::SetEnvironmentVariable('GITHUB_INVESTIGATION_TOKEN', $savedEnvironment, 'Process') }
"PASS: $script:assertions assertions on PowerShell $($PSVersionTable.PSVersion); offline only."
