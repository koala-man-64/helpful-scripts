#requires -Version 7.2
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/Investigate-TerraformToken.ps1"
$script:checks = 0
function Assert($Condition, [string]$Label) {
    if (-not $Condition) { throw "FAILED: $Label" }
    $script:checks++
}
function New-Account([string]$Type = 'users', [string]$Id = 'user-Abc') {
    return @{
        type='users'; id='user-Abc'; attributes=@{username='alice'; 'is-service-account'=($Type -ne 'users')}
        relationships=@{'authenticated-resource'=@{data=@{type=$Type; id=$Id}; links=@{related="/api/v2/$Type/$Id"}}}
        links=@{'auth-token'='/api/v2/authentication-tokens/at-Abc'}
    }
}
function Run-Case($Account, $Extra = @{}) {
    $script:paths = [System.Collections.Generic.List[string]]::new()
    $recordedPaths = $script:paths
    $map = @{'/api/v2/account/details'=@{status='ok'; httpStatus=200; data=$Account}}
    foreach ($key in $Extra.Keys) { $map[$key] = $Extra[$key] }
    $get = {
        param($path)
        $recordedPaths.Add($path)
        if ($map.ContainsKey($path)) { return $map[$path] }
        return @{status='http_403'; httpStatus=403; data=$null}
    }.GetNewClosure()
    return Invoke-TerraformInvestigation 'https://terraform.example.test' 'FAKE_SECRET_FOR_TEST_ONLY' $get
}

$parseErrors = $null; $lexemes = $null
[void][Management.Automation.Language.Parser]::ParseFile("$PSScriptRoot/Investigate-TerraformToken.ps1", [ref]$lexemes, [ref]$parseErrors)
Assert ($parseErrors.Count -eq 0) 'syntax'
$meta = @{status='ok'; httpStatus=200; data=@{type='authentication-tokens'; id='at-Abc'; attributes=@{description='build'; token='DO_NOT_OUTPUT'}}}
$r = Run-Case (New-Account) @{'/api/v2/authentication-tokens/at-Abc'=$meta}
Assert ($r.status -eq 'identified' -and $r.owner.name -eq 'alice' -and $r.token.description -eq 'build') 'user identity and exact metadata'
Assert (($r | ConvertTo-Json -Depth 8) -notmatch 'DO_NOT_OUTPUT') 'metadata excludes secret field'
foreach ($case in @(@('teams','team-Abc'), @('organizations','my-org'))) {
    $a = New-Account $case[0] $case[1]
    $detail = @{status='ok'; httpStatus=200; data=@{type=$case[0]; id=$case[1]; attributes=@{name='owner-name'}}}
    $r = Run-Case $a @{("/api/v2/"+$case[0]+'/'+$case[1])=$detail}
    Assert ($r.owner.type -eq $case[0] -and $r.owner.name -eq 'owner-name' -and $r.token.id -eq 'at-Abc') "owner $($case[0])"
    Assert ($script:paths[0] -eq '/api/v2/account/details') 'account first'
}
$a = New-Account 'teams' 'team-Abc'; $a.relationships = @{}
$r = Run-Case $a
Assert ($null -eq $r.owner.type -and $r.status -eq 'partial') 'missing service-user relationship is unknown'
$a = New-Account; $a.relationships = @{}
$r = Run-Case $a
Assert ($r.owner.evidence -eq 'account_non_service_user') 'explicit non-service fallback'
$a.attributes.Remove('is-service-account'); $a.attributes.username = 'api-team_name'
$r = Run-Case $a
Assert ($null -eq $r.owner.type) 'username cannot classify'
$a = New-Account; $a.links = @{}
$r = Run-Case $a
Assert ($r.status -eq 'partial' -and $null -eq $r.token.id) 'missing current-token link'
$r = Run-Case (New-Account)
Assert ($r.token.id -eq 'at-Abc' -and $r.token.evidence -eq 'account_auth-token_link') 'denial retains exact identity'
$r = Run-Case (New-Account) @{'/api/v2/account/details'=@{status='http_401'; httpStatus=401}}
Assert ($r.status -eq 'unresolved' -and $script:paths.Count -eq 1) 'invalid authentication stops'
$r = Run-Case @{type='workspaces'; id='ws-Abc'}
Assert ($r.status -eq 'unresolved') 'unexpected resource'
$badMeta = @{status='ok'; httpStatus=200; data=@{type='authentication-tokens'; id='at-Other'; attributes=@{description='wrong'}}}
$r = Run-Case (New-Account) @{'/api/v2/authentication-tokens/at-Abc'=$badMeta}
Assert ($null -eq $r.token.description -and $r.token.id -eq 'at-Abc') 'metadata identity mismatch'
$badMeta.data.id='at-Abc'; $badMeta.data.attributes.description="FAKE_SECRET_FOR_TEST_ONLY`nabc.atlasv1.def"
$r = Run-Case (New-Account) @{'/api/v2/authentication-tokens/at-Abc'=$badMeta}
Assert (($r | ConvertTo-Json -Depth 8) -notmatch 'FAKE_SECRET_FOR_TEST_ONLY|abc.atlasv1.def') 'description redaction'
$badMeta.data.attributes.token = 'different-returned-credential'
$badMeta.data.attributes.description = 'label different-returned-credential'
$r = Run-Case (New-Account) @{'/api/v2/authentication-tokens/at-Abc'=$badMeta}
Assert ($r.token.description -eq 'label [REDACTED]') 'returned credential redaction'
$longCredential = 'z' * 300
Assert ((Get-SafeText "label $longCredential" 'fixture' @($longCredential)) -eq 'label [REDACTED]') 'redact before truncation'
$a = New-Account; $a.id = @('user-Abc', 'user-Other')
Assert ((Run-Case $a).status -eq 'unresolved') 'non-scalar identity rejected'
$a = New-Account 'teams' 'team-Abc'; $a.relationships.'authenticated-resource'.data.type = 'groups'
Assert ((Run-Case $a).owner.evidence -eq 'unavailable') 'unsupported relationship type'
foreach ($bad in @('http://terraform.example.test', 'terraform.example.test', 'https://user@terraform.example.test', 'https://terraform.example.test/path', 'https://terraform.example.test/?x=1', 'https://terraform.example.test/#x', 'https://terraform.example.test\evil')) {
    Assert ($null -eq (Get-ApprovedOrigin $bad)) 'unsafe origin rejected'
}
Assert ((Get-ApprovedOrigin 'https://terraform.example.test:8443/') -eq 'https://terraform.example.test:8443') 'explicit HTTPS port'
foreach ($bad in @('https://evil.test/api/v2/authentication-tokens/at-Abc', '//evil.test/api/v2/authentication-tokens/at-Abc', '/api/v2/workspaces', '/api/v2/authentication-tokens/at-Abc?x=1', '/api/v2/authentication-tokens/%61t-Abc', '/api/v2/x/../authentication-tokens/at-Abc', 'https://terraform.example.test/api/v2/x/../authentication-tokens/at-Abc', 'https://terraform.example.test:444/api/v2/authentication-tokens/at-Abc', 'https://user@terraform.example.test/api/v2/authentication-tokens/at-Abc')) {
    $a = New-Account; $a.links.'auth-token'=$bad
    $r = Run-Case $a
    Assert ($null -eq $r.token.id -and $script:paths.Count -eq 1) 'unsafe credential link never fetched'
}
$a = New-Account 'teams' 'team-Abc'; $a.relationships.'authenticated-resource'.links.related='https://evil.test/api/v2/teams/team-Abc'
$r = Run-Case $a
Assert (-not ($script:paths -match 'evil.test') -and $r.owner.id -eq 'team-Abc') 'unsafe owner link retains relationship only'

# Real HttpClient with an in-memory handler: no DNS, sockets or live credentials.
Add-Type -TypeDefinition @'
using System;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Generic;
public class InvestigationFakeHandler : HttpMessageHandler {
    public Queue<HttpResponseMessage> Replies = new Queue<HttpResponseMessage>();
    public int Calls;
    public bool Fail;
    public bool Delay;
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) {
        Calls++;
        if (request.Method != HttpMethod.Get) throw new Exception("non-GET");
        if (Fail) throw new Exception("FAKE_SECRET_FOR_TEST_ONLY Authorization Bearer private body");
        if (Delay) return Delayed(ct);
        return Task.FromResult(Replies.Dequeue());
    }
    private async Task<HttpResponseMessage> Delayed(CancellationToken ct) {
        await Task.Delay(2000, ct);
        return new HttpResponseMessage(HttpStatusCode.OK);
    }
}
'@
function New-Reply([int]$Status, [string]$Body = '{"data":{"type":"users","id":"user-Abc"}}', [string]$Media = 'application/vnd.api+json') {
    $reply = [Net.Http.HttpResponseMessage]::new([Net.HttpStatusCode]$Status)
    $reply.Content = [Net.Http.StringContent]::new($Body, [Text.Encoding]::UTF8, $Media)
    return $reply
}
function Run-Http($Replies, [switch]$Fail) {
    $script:fake = [InvestigationFakeHandler]::new(); $script:fake.Fail = $Fail
    foreach ($reply in $Replies) { $script:fake.Replies.Enqueue($reply) }
    $client = [Net.Http.HttpClient]::new($script:fake)
    $client.MaxResponseContentBufferSize = 1048576
    try { return Invoke-TerraformGet $client 'https://terraform.example.test' '/api/v2/account/details' }
    finally { $client.Dispose() }
}
foreach ($status in @(401,403,404,302,500)) {
    $r = Run-Http @((New-Reply $status 'FAKE_SECRET_FOR_TEST_ONLY'))
    Assert ($r.status -eq "http_$status" -and $script:fake.Calls -eq 1 -and $null -eq $r.data) "HTTP $status fixed result"
}
$r = Run-Http @((New-Reply 200 '<html>private</html>' 'text/html'))
Assert ($r.status -eq 'unexpected_content_type') 'non JSON rejected'
$r = Run-Http @((New-Reply 200 'FAKE_SECRET_FOR_TEST_ONLY'))
Assert ($r.status -eq 'invalid_json') 'invalid JSON safe'
foreach ($body in @('{"data":[]}', 'null', '{"errors":[]}')) {
    $r = Run-Http @((New-Reply 200 $body))
    Assert ($r.status -eq 'unexpected_document') 'unexpected JSON shape'
}
$r = Run-Http @() -Fail
Assert ($r.status -eq 'transport_error' -and ($r | ConvertTo-Json) -notmatch 'FAKE_SECRET|Authorization|private') 'exception details suppressed'
$reply = New-Reply 429; $reply.Headers.RetryAfter = [Net.Http.Headers.RetryConditionHeaderValue]::new([TimeSpan]::Zero)
$r = Run-Http @($reply, (New-Reply 200))
Assert ($r.status -eq 'ok' -and $script:fake.Calls -eq 2) 'rate limit retry'
$replies = 1..3 | ForEach-Object { $reply = New-Reply 503; $reply.Headers.RetryAfter = [Net.Http.Headers.RetryConditionHeaderValue]::new([TimeSpan]::Zero); $reply }
$r = Run-Http $replies
Assert ($r.status -eq 'http_503' -and $script:fake.Calls -eq 3) 'retry bound'
$reply = New-Reply 429; $reply.Headers.RetryAfter = [Net.Http.Headers.RetryConditionHeaderValue]::new([TimeSpan]::FromSeconds(60))
$r = Run-Http @($reply)
Assert ($r.status -eq 'http_429' -and $script:fake.Calls -eq 1) 'long Retry-After stops'
$r = Run-Http @((New-Reply 200 ('x' * 1048577)))
Assert ($r.status -eq 'transport_error') 'body size bounded'
$handler = [InvestigationFakeHandler]::new(); $handler.Delay = $true
$client = [Net.Http.HttpClient]::new($handler); $client.Timeout = [TimeSpan]::FromMilliseconds(20)
try {
    $r = Invoke-TerraformGet $client 'https://terraform.example.test' '/api/v2/account/details'
    Assert ($r.status -eq 'transport_error' -and $handler.Calls -eq 1) 'timeout not retried'
}
finally { $client.Dispose() }

# Exercise the real credential setup/entry path, replacing only the network seam.
$savedTransport = ${function:Invoke-TerraformGet}
$script:entryCalls = 0
function Invoke-TerraformGet($Client, $BaseOrigin, $Path) {
    $script:entryCalls++
    Assert ($Client.DefaultRequestHeaders.Authorization.Scheme -eq 'Bearer') 'Bearer header configured'
    Assert ($Client.DefaultRequestHeaders.Authorization.Parameter -eq 'FAKE_SECRET_FOR_TEST_ONLY') 'SecureString consumed'
    Assert ($Client.Timeout.TotalSeconds -eq 20 -and $Client.MaxResponseContentBufferSize -eq 1048576) 'production limits configured'
    if ($Path -eq '/api/v2/account/details') { return @{status='ok'; httpStatus=200; data=(New-Account)} }
    return @{status='http_404'; httpStatus=404; data=$null}
}
$Origin = 'https://terraform.example.test'
$Token = ConvertTo-SecureString 'FAKE_SECRET_FOR_TEST_ONLY' -AsPlainText -Force
$NonInteractive = $true
try {
    $r = Start-TerraformInvestigation
    Assert ($r.status -eq 'identified' -and $script:entryCalls -eq 2) 'entry path completes with mocked network'
    Assert (($r | ConvertTo-Json -Depth 8) -notmatch 'FAKE_SECRET_FOR_TEST_ONLY') 'entry output secret safe'
}
finally { $Token.Dispose(); $Token = $null; ${function:Invoke-TerraformGet} = $savedTransport }
Write-Output "PASS: $script:checks assertions on PowerShell $($PSVersionTable.PSVersion); offline only."
