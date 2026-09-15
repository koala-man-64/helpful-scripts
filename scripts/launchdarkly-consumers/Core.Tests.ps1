# PowerShell 7 offline assertions. All fixtures are synthetic; no network or accounts.
$ErrorActionPreference='Stop'
. "$PSScriptRoot/Core.ps1"
. "$PSScriptRoot/Configuration.ps1"
. "$PSScriptRoot/LaunchDarkly.ps1"
$script:passed=0
function Assert-LdcTest([bool]$Condition,[string]$Name) {
    if (-not $Condition) { throw "FAIL: $Name" }
    $script:passed++
}
function Assert-LdcThrows([scriptblock]$Action,[string]$Expected,[string]$Name) {
    $actual='none'
    try { & $Action | Out-Null } catch { $actual=$_.Exception.Message }
    Assert-LdcTest ($actual -eq $Expected) $Name
}
function Reset-LdcTest {
    $config=@{launchDarkly=@{projectKey='project';environmentKey='production';credentialResourceKey='credential'};
        azure=@{subscriptionIds=@('00000000-0000-0000-0000-000000000001')};gcp=@{projectIds=@('test-project')};
        github=@{organizations=@('test-org');repositories=@('other/repo')};azureDevOps=@(@{organization='org';projects=@('Test Project')})}
    Initialize-LdcState $config 'sdk-test-secret-canary' 'management-canary' -From ([datetimeoffset]'2026-09-01T00:00:00Z') -To ([datetimeoffset]'2026-09-03T00:00:00Z')
}
Reset-LdcTest
Assert-LdcTest (Test-LdcSecret 'sdk-test-secret-canary') 'exact ordinal match'
Assert-LdcTest (-not (Test-LdcSecret 'SDK-test-secret-canary')) 'case mismatch'
Assert-LdcTest (-not (Test-LdcSecret ' sdk-test-secret-canary')) 'whitespace not normalized'
Register-LdcSensitive 'private-payload-canary'
Add-LdcFinding -Platform Azure -Scope scope -Resource 'sdk-test-secret-canary' -Classification ExactStoredKeyMatch -Owner 'private-payload-canary' -Evidence 'management-canary'
$json=(Get-LdcReport | ConvertTo-Json -Depth 20)
Assert-LdcTest ($json -notmatch 'sdk-test-secret-canary|private-payload-canary|management-canary') 'report redaction'
Assert-LdcTest ((Get-LdcReport).consumerCompleteness -eq 'not-established') 'consumer completeness never asserted'
$script:Ldc.SensitiveCharacters=32MB
Assert-LdcThrows { Register-LdcSensitive 'additional-payload-canary' } 'LDC:sensitive-memory-limit' 'redaction memory is bounded'
Assert-LdcTest (Test-LdcSecret 'sdk-test-secret-canary') 'known match remains available at memory bound'
Reset-LdcTest
Assert-LdcThrows { Assert-LdcRequest LaunchDarkly ([uri]'https://evil.example/api/v2/applications') GET } 'LDC:request-outside-scope' 'external host'
Assert-LdcThrows { Assert-LdcRequest LaunchDarkly ([uri]'http://app.launchdarkly.com/api/v2/applications') GET } 'LDC:unsafe-request' 'insecure scheme'
Assert-LdcThrows { Assert-LdcRequest LaunchDarkly ([uri]'https://user@app.launchdarkly.com/api/v2/applications') GET } 'LDC:unsafe-request' 'userinfo'
Assert-LdcThrows { Assert-LdcRequest GitHub ([uri]'https://api.github.com/repos/unlisted/private/contents/file') GET } 'LDC:request-outside-scope' 'unlisted repository'
Assert-LdcThrows { Assert-LdcRequest GitHub ([uri]'https://api.github.com/repos/other/repo/actions/workflows/1/dispatches') POST } 'LDC:request-outside-scope' 'workflow execution'
Assert-LdcThrows { Assert-LdcRequest Azure ([uri]'https://management.azure.com/subscriptions/00000000-0000-0000-0000-000000000002/resources') GET } 'LDC:request-outside-scope' 'other subscription'
Assert-LdcThrows { Assert-LdcRequest KeyVault ([uri]'https://unverified.vault.azure.net/secrets') GET } 'LDC:request-outside-scope' 'unverified vault'
Assert-LdcRequest AzureDevOps ([uri]'https://dev.azure.com/org/Test%20Project/_apis/git/repositories') GET
Assert-LdcTest $true 'encoded configured project'
Assert-LdcThrows { Assert-LdcRequest GCP ([uri]'https://secretmanager.googleapis.com/v1/projects/other-project/secrets') GET } 'LDC:request-outside-scope' 'other GCP project'
Reset-LdcTest
Invoke-LdcScope Azure scope {
    Add-LdcFinding -Platform Azure -Scope scope -Resource retained -Classification ExactStoredKeyMatch
    throw 'failure with private-payload-canary'
}
$report=Get-LdcReport
Assert-LdcTest ($report.status -eq 'incomplete' -and $report.scopes[0].Status -eq 'failed') 'failed scope is incomplete'
Assert-LdcTest (@($report.findings | Where-Object Classification -eq ExactStoredKeyMatch).Count -eq 1) 'retains partial evidence'
Assert-LdcTest (($report | ConvertTo-Json -Depth 20) -notmatch 'private-payload-canary') 'discards raw error'
Reset-LdcTest
Add-LdcFinding -Platform GCP -Scope p -Resource match -Classification ExactStoredKeyMatch -SecretReference 'projects/p/secrets/s' -SecretVersion '1'
Add-LdcFinding -Platform GitHub -Scope repo -Resource pinned -Classification UnresolvedCandidate -SecretReference 'projects/p/secrets/s' -SecretVersion '1'
Add-LdcFinding -Platform GitHub -Scope repo -Resource alias -Classification UnresolvedCandidate -SecretReference 'projects/p/secrets/s' -SecretVersion latest
Add-LdcFinding -Platform GitHub -Scope repo -Resource unversioned -Classification UnresolvedCandidate -SecretReference 'projects/p/secrets/s'
$report=Get-LdcReport
Assert-LdcTest (@($report.findings | Where-Object Classification -eq ConfigurationReferenceLinkedToMatch).Count -eq 1) 'only pinned version links'
Assert-LdcTest (@($report.findings | Where-Object Classification -eq UnresolvedCandidate).Count -eq 2) 'historical match not current'
Reset-LdcTest
$script:Ldc.GcpProjectAliases['test-project']='123456789'
Add-LdcFinding -Platform GCP -Scope p -Resource match -Classification ExactStoredKeyMatch -SecretReference 'projects/123456789/secrets/UpperCase' -SecretVersion '4'
Add-LdcFinding -Platform GitHub -Scope repo -Resource pinned -Classification UnresolvedCandidate -SecretReference 'projects/test-project/secrets/UpperCase' -SecretVersion '4'
Add-LdcFinding -Platform GitHub -Scope repo -Resource different -Classification UnresolvedCandidate -SecretReference 'projects/test-project/secrets/uppercase' -SecretVersion '4'
$report=Get-LdcReport
Assert-LdcTest (@($report.findings | Where-Object Classification -eq ConfigurationReferenceLinkedToMatch).Count -eq 1) 'verified project alias joins but name case remains exact'

function Get-LdcToken { param($Platform,$Subscription) return 'fake-token' }
function Start-Sleep { param($Milliseconds) $script:delays.Add($Milliseconds) }
function Invoke-LdcHttpAttempt {
    param($Platform,$Uri,$Method,$Token,$Body)
    $script:httpCalls++
    return $script:responses.Dequeue()
}
function Reset-LdcHttp([object[]]$Responses) {
    $script:responses=[Collections.Generic.Queue[object]]::new()
    foreach ($response in $Responses) { $script:responses.Enqueue($response) }
    $script:httpCalls=0
    $script:delays=[Collections.Generic.List[int]]::new()
}
Reset-LdcTest
Reset-LdcHttp @(@{Status=429;Headers=@{'Retry-After'='1'};Text='raw-canary'},@{Status=200;Headers=@{};Text='{"items":[]}'})
$r=Invoke-LdcRequest LaunchDarkly 'https://app.launchdarkly.com/api/v2/applications'
Assert-LdcTest ($script:httpCalls -eq 2 -and $script:delays[0] -eq 1000) 'honors Retry-After'
Reset-LdcHttp @(@{Status=200;Headers=@{};Text='[]'})
$r=Invoke-LdcRequest GitHub 'https://api.github.com/orgs/test-org/repos'
Assert-LdcTest ($r.Data -is [array] -and $r.Data.Count -eq 0) 'empty top-level API array retained'
Reset-LdcHttp @(@{Status=200;Headers=@{};Text='[{"id":1,"name":"one"}]'})
$r=Invoke-LdcRequest GitHub 'https://api.github.com/orgs/test-org/repos'
Assert-LdcTest ($r.Data -is [array] -and $r.Data.Count -eq 1) 'single top-level API array retained'
Reset-LdcHttp @(@{Status=429;Headers=@{'Retry-After'='61'};Text='raw-canary'})
Assert-LdcThrows { Invoke-LdcRequest LaunchDarkly 'https://app.launchdarkly.com/api/v2/applications' } 'LDC:retry-window-exceeded' 'long retry is explicit gap'
Reset-LdcHttp @(@{Status=401;Headers=@{};Text='raw-canary'})
Assert-LdcThrows { Invoke-LdcRequest LaunchDarkly 'https://app.launchdarkly.com/api/v2/applications' } 'LDC:http-401' 'expired authentication'
Assert-LdcTest ($script:httpCalls -eq 1) '401 not retried'
Reset-LdcHttp @(@{Status=302;Headers=@{Location='https://evil.example'};Text=''})
Assert-LdcThrows { Invoke-LdcRequest LaunchDarkly 'https://app.launchdarkly.com/api/v2/applications' } 'LDC:http-302' 'redirect rejected'
Reset-LdcHttp @(@{Status=200;Headers=@{};Text='private-json-error-canary'})
Assert-LdcThrows { Invoke-LdcRequest LaunchDarkly 'https://app.launchdarkly.com/api/v2/applications' } 'LDC:invalid-json' 'sanitized parse error'
Assert-LdcThrows { Invoke-LdcRequest Azure 'https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01' POST '{"subscriptions":["outside"],"query":"Resources"}' } 'LDC:request-outside-scope' 'read POST scope'

Reset-LdcTest
$from=$script:Ldc.From.ToUnixTimeMilliseconds()
$later=$from+86400000
Reset-LdcHttp @(
    @{Status=200;Headers=@{};Text='{"items":[{"key":"app-one","name":"Application One"}],"totalCount":2}'},
    @{Status=200;Headers=@{};Text='{"items":[{"key":"app-two","name":"Application Two"}],"totalCount":2}'},
    @{Status=200;Headers=@{};Text=(@{metadata=@(@{sdkAppId='app-one';sdkName='dotnet';sdkVersion='1';connectionType='streaming'},@{sdkName='python'});series=@(@{time=$from;'0'=5;'1'=2});projectedSeries=@(@{time=$later;'0'=100})} | ConvertTo-Json -Depth 10)}
)
Invoke-LdcLaunchDarkly
$report=Get-LdcReport
$activity=@($report.findings | Where-Object Classification -eq EnvironmentActivity)
Assert-LdcTest ($script:httpCalls -eq 3 -and $activity.Count -eq 2) 'short applications page continues'
Assert-LdcTest ($activity[0].Application -eq 'Application One') 'metadata joins activity'
Assert-LdcTest ($activity[0].FirstObservedBucket -eq $activity[0].LastObservedBucket) 'projected usage excluded'
Assert-LdcTest ($report.status -eq 'incomplete' -and $activity[1].Application -eq 'Unknown') 'unknown activity retained'
Reset-LdcTest
$script:Ldc.MaxPages=2
Reset-LdcHttp @(
    @{Status=200;Headers=@{};Text='{"items":[{"key":"app-one"}]}'},
    @{Status=200;Headers=@{};Text='{"items":[{"key":"app-one"}]}'},
    @{Status=200;Headers=@{};Text='{"metadata":[],"series":[]}'}
)
Invoke-LdcLaunchDarkly
Assert-LdcTest ((Get-LdcReport).status -eq 'incomplete') 'repeated applications page fails closed'
$config=Read-LdcConfiguration "$PSScriptRoot/config.example.json"
Assert-LdcTest ($config.azure.subscriptionIds.Count -eq 0 -and $config.github.repositories.Count -eq 0) 'empty explicit platform scopes'
Assert-LdcThrows { Read-LdcConfiguration "$PSScriptRoot/nonexistent-config.json" } 'LDC:invalid-configuration' 'unavailable configuration'

# Exercise bounded native process lifetime using this PowerShell executable as a
# synthetic CLI. No real provider CLI is called by this test seam.
$script:FakeCliPath=(Get-Process -Id $PID).Path
function Get-Command { return $null }
Assert-LdcThrows { Invoke-LdcNative gh @('auth','token') } 'LDC:missing-gh' 'missing CLI is explicit'
function Get-Command { return [pscustomobject]@{Source=$script:FakeCliPath} }
Reset-LdcTest
$value=Invoke-LdcNative gh @('-NoProfile','-Command',"[Console]::Out.Write('synthetic-result')")
Assert-LdcTest ($value -eq 'synthetic-result') 'native stdout capture'
Assert-LdcThrows { Invoke-LdcNative gh @('-NoProfile','-Command',"[Console]::Out.Write(('x'*70000))") } 'LDC:cli-output-limit' 'native stdout is capped while reading'
Assert-LdcThrows { Invoke-LdcNative gh @('-NoProfile','-Command',"[Console]::Error.Write(('x'*70000))") } 'LDC:cli-output-limit' 'native stderr is capped while reading'
Assert-LdcThrows { Invoke-LdcNative gh @('-NoProfile','-Command',"[Console]::Error.Write('private-error-canary'); exit 1") } 'LDC:gh-authentication-failed' 'native failure cannot leak stderr'
$script:Ldc.TimeoutSeconds=1
Assert-LdcThrows { Invoke-LdcNative gh @('-NoProfile','-Command','Start-Sleep -Seconds 15') } 'LDC:cli-timeout' 'native timeout terminates owned helper'

# Synthetic report roundtrip; remove only the two files and one config created here.
$testDirectory=Join-Path ([IO.Path]::GetTempPath()) ('ldc-offline-' + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($testDirectory)
try {
    Reset-LdcTest
    Add-LdcFinding -Platform GitHub -Scope '=HYPERLINK("bad")' -Resource '+formula' -Classification UnresolvedCandidate -Owner "`t@formula"
    Add-LdcFinding -Platform Azure -Scope test -Resource 'sdk-test-secret-canary' -Classification ExactStoredKeyMatch -Evidence 'management-canary'
    $status=Write-LdcReport $testDirectory
    $csvPath=Join-Path $testDirectory 'launchdarkly-consumers.csv'
    $jsonPath=Join-Path $testDirectory 'launchdarkly-consumers.json'
    $csv=[IO.File]::ReadAllText($csvPath)
    $json=[IO.File]::ReadAllText($jsonPath)
    Assert-LdcTest ($csv.Contains("'=HYPERLINK") -and $csv.Contains("'+formula") -and $csv.Contains("' @formula")) 'CSV formula protection'
    Assert-LdcTest (($csv+$json) -notmatch 'sdk-test-secret-canary|management-canary') 'persisted files exclude sensitive canaries'
    Assert-LdcThrows { Write-LdcReport $testDirectory } 'LDC:output-already-exists' 'existing reports protected'
    $configPath=Join-Path $testDirectory 'config.json'
    foreach ($fixture in @(
        '{"launchDarkly":{"projectKey":"p","environmentKey":"e","credentialResourceKey":"k"},"github":{"repositories":"owner/repo"}}',
        '{"launchDarkly":{"projectKey":"p","environmentKey":"e","credentialResourceKey":"k"},"github":{"repositories":["owner/../evil"]}}',
        '{"launchDarkly":{"projectKey":"p","environmentKey":"e","credentialResourceKey":"k"},"github":{"repository":["owner/repo"]}}'
    )) {
        [IO.File]::WriteAllText($configPath,$fixture)
        Assert-LdcThrows { Read-LdcConfiguration $configPath } 'LDC:invalid-configuration' 'bad scope shape rejected'
    }
} finally {
    $resolved=[IO.Path]::GetFullPath($testDirectory)
    $expectedPrefix=Join-Path ([IO.Path]::GetFullPath([IO.Path]::GetTempPath())) 'ldc-offline-'
    if ($resolved.StartsWith($expectedPrefix,[StringComparison]::OrdinalIgnoreCase)) {
        foreach ($name in @('launchdarkly-consumers.csv','launchdarkly-consumers.json','config.json')) {
            [IO.File]::Delete((Join-Path $resolved $name))
        }
        [IO.Directory]::Delete($resolved, $false)
    }
}
Write-Output 'Core offline assertions passed.'
