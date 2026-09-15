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
function Run-Case($Identity = (New-Identity), $RepoReply = $null, [string]$Repo = '', [string]$Credential = 'FAKE_FIXTURE_ONLY', [string]$EnterpriseName = '', $AuditReply = $null, [int]$Days = 30) {
    $script:paths = [Collections.Generic.List[string]]::new()
    $calls = $script:paths
    $get = { param($path) $calls.Add($path); if ($path -eq '/user') { return $Identity }; return $RepoReply }.GetNewClosure()
    $auditGet = { param($hash, $enterprise, $days) $calls.Add("audit:${enterprise}:${days}:$($hash.Length):$($hash -cmatch '^[A-Za-z0-9+/]{43}=$')"); return $AuditReply }.GetNewClosure()
    return Invoke-GhInvestigation $Credential $Repo $get $EnterpriseName $Days $auditGet
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
$audit = @{status='ok'; httpStatus=200; eventCount=12; pageCount=2; completeness='complete_for_returned_audit_events'; windowStartDateUtc='2026-08-17'; windowDays=30; averageEventsPerDay=0.4}
$r = Run-Case -EnterpriseName 'octo-enterprise' -AuditReply $audit
Assert ($r.status -eq 'identified' -and $r.auditUsage.eventCount -eq 12 -and $r.auditUsage.averageEventsPerDay -eq 0.4 -and $r.auditUsage.retentionLimited) 'audit usage summarized'
Assert ($script:paths[1] -eq 'audit:octo-enterprise:30:44:True') 'audit uses hash and bounded window'
$expectedHash=Get-GhTokenHash 'FAKE_FIXTURE_ONLY'
Assert (($r | ConvertTo-Json -Depth 8) -notmatch [regex]::Escape($expectedHash)) 'token hash not emitted'
$audit.completeness='truncated_at_page_limit'
Assert ((Run-Case -EnterpriseName 'octo-enterprise' -AuditReply $audit).status -eq 'partial') 'truncated audit is partial'
$audit.status='http_403'; $audit.eventCount=$null; $audit.completeness='failed'
Assert ((Run-Case -EnterpriseName 'octo-enterprise' -AuditReply $audit).status -eq 'partial') 'audit failure preserves identity as partial'
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
    public Uri LastUri;
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage req, CancellationToken ct) {
        Calls++;
        LastUri = req.RequestUri;
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
function Run-AuditHttp([int]$Status=200, [string]$Body='[]', [string]$Media='application/json', [string]$Link='', [int]$MaxPages=100, [string]$Base='https://api.github.com', [string]$EnterpriseName='octo-enterprise', [string]$Hash='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=') {
    $script:handler = [GitHubFixtureHandler]::new()
    $reply = [Net.Http.HttpResponseMessage]::new([Net.HttpStatusCode]$Status)
    $reply.Content = [Net.Http.StringContent]::new($Body, [Text.Encoding]::UTF8, $Media)
    if ($Link) { $reply.Headers.TryAddWithoutValidation('Link', $Link) | Out-Null }
    $script:handler.Reply=$reply
    $client=[Net.Http.HttpClient]::new($script:handler)
    $client.MaxResponseContentBufferSize=1048576
    try { return Invoke-GhAuditSearch $client $Base $EnterpriseName $Hash 30 $MaxPages }
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
$r=Run-AuditHttp -Body '[{"action":"repo.create"},{"action":"repo.destroy"}]'
Assert ($r.status -eq 'ok' -and $r.eventCount -eq 2 -and $r.averageEventsPerDay -eq 0.07 -and $r.completeness -eq 'complete_for_returned_audit_events') 'audit events counted'
Assert ($script:handler.Calls -eq 1 -and $script:handler.LastUri.AbsolutePath -eq '/enterprises/octo-enterprise/audit-log') 'audit endpoint constrained'
Assert ([uri]::UnescapeDataString($script:handler.LastUri.Query) -match 'hashed_token:"A{43}="' -and $script:handler.LastUri.Query -match 'include=all') 'audit query hashes and includes Git events'
$start=[DateTime]::UtcNow.Date.AddDays(-29).ToString('yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$phrase=[uri]::EscapeDataString('hashed_token:' + [char]34 + 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=' + [char]34 + ' created:>=' + $start)
$next='https://api.github.com/enterprises/octo-enterprise/audit-log?phrase=' + $phrase + '&include=all&per_page=100&after=cursor'
$r=Run-AuditHttp -Body '[]' -Link ('<' + $next + '>; rel="next"') -MaxPages 1
Assert ($r.completeness -eq 'truncated_at_page_limit' -and $r.pageCount -eq 1) 'audit page cap reported'
$r=Run-AuditHttp -Body '[]' -Link '<https://evil.test/audit-log?include=all>; rel="next"' -MaxPages 1
Assert ($r.status -eq 'unsafe_pagination' -and $null -eq $r.eventCount) 'unsafe audit pagination rejected'
$wrongDate=[DateTime]::UtcNow.Date.AddDays(-30).ToString('yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$wrongPhrase=[uri]::EscapeDataString('hashed_token:' + [char]34 + 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=' + [char]34 + ' created:>=' + $wrongDate)
$wrongNext='https://api.github.com/enterprises/octo-enterprise/audit-log?phrase=' + $wrongPhrase + '&include=all&per_page=100&after=cursor'
$r=Run-AuditHttp -Body '[]' -Link ('<' + $wrongNext + '>; rel="next"') -MaxPages 1
Assert ($r.status -eq 'unsafe_pagination') 'changed audit window rejected during pagination'
foreach ($case in @(
    @{Base='https://enterprise.example.test/api/v3'; EnterpriseName='octo-enterprise'; Hash='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='},
    @{Base='https://api.github.com'; EnterpriseName='../evil'; Hash='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='},
    @{Base='https://api.github.com'; EnterpriseName='octo-enterprise'; Hash='bad'}
)) {
    Assert ((Run-AuditHttp -Base $case.Base -EnterpriseName $case.EnterpriseName -Hash $case.Hash).status -eq 'unsafe_endpoint') 'unsafe audit target rejected'
}
foreach ($status in @(401,403,429,500)) { Assert ((Run-AuditHttp -Status $status).status -eq "http_$status") 'audit HTTP failure bounded' }
Assert ((Run-AuditHttp -Body '{}').status -eq 'unexpected_document') 'audit object rejected'
Assert ((Run-AuditHttp -Body '{').status -eq 'invalid_json') 'audit invalid JSON rejected'
Assert ((Run-AuditHttp -Media 'text/html').status -eq 'unexpected_content_type') 'audit HTML rejected'
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
    $ApiBaseUrl='https://api.github.com'; $AllowEnterpriseHost=$false; $Enterprise='octo-enterprise'; $NonInteractive=$true
    Assert ((Start-GhInvestigation).status -eq 'configuration_error') 'enterprise audit requires second credential'
    $AuditToken=ConvertTo-SecureString $script:entryFixture -AsPlainText -Force
    Assert ((Start-GhInvestigation).status -eq 'configuration_error') 'same credential rejected for audit'
}
finally { $Token.Dispose(); $Token=$null; if ($null -ne $AuditToken) { $AuditToken.Dispose(); $AuditToken=$null }; $Enterprise=''; $NonInteractive=$false; ${function:Invoke-GhGet}=$savedTransport }
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
