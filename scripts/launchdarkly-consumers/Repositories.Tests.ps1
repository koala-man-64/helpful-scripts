# Self-contained PowerShell 7 regression harness; no Pester/module dependency.
$here = Split-Path -Parent $PSCommandPath
. "$here/Core.ps1"
. "$here/Repositories.ps1"
function Assert-Ldc { param([bool]$Condition,[string]$Message) if(-not $Condition){ throw "ASSERT: $Message" } }
function New-LdcTestState {
    Initialize-LdcState -Config @{ github=@{organizations=@();repositories=@('acme/demo')};azureDevOps=@();maxReferenceDepth=4;maxContentCharacters=4096 } -KnownSecret 'ld-secret-test' -ManagementToken 'test-token'
    $script:TestRequests=[Collections.Generic.List[string]]::new()
}
function Invoke-LdcRequest {
    param($Platform,$Uri,$Method,$Body,[switch]$Raw)
    $script:TestRequests.Add($Uri)
    if($Uri -match '/repos/acme/demo\?'){ return @{Data=@{full_name='acme/demo';default_branch='main'};Headers=@{}} }
    if($Uri -match '/actions/workflows'){ return @{Data=@{workflows=@(@{id=1;name='build';path='.github/workflows/build.yml'})};Headers=@{}} }
    if($Uri -match '/contents/'){ return @{Data="uses: external/nope/.github/workflows/reuse.yml@v1`nLD_SDK_KEY: ld-secret-test`nprojects/proj/secrets/KeyName/versions/7";Headers=@{}} }
    if($Uri -match '/git/trees/'){ return @{Data=@{tree=@()};Headers=@{}} }
    return @{Data=@{secrets=@();environments=@();value=@()};Headers=@{}}
}
New-LdcTestState; Invoke-LdcRepositories
$all = $script:Ldc.Findings | Out-String
Assert-Ldc (@($script:TestRequests -match 'api.github.com/repos/acme/demo').Count -gt 0) 'configured repository was not requested'
Assert-Ldc ($all -match 'ExactSourceKeyMatch') 'exact source match absent'
Assert-Ldc ($all -match 'OFF_SCOPE_REFERENCE_NOT_FOLLOWED') 'off-scope reusable workflow gap absent'
Assert-Ldc ($all -notmatch 'ld-secret-test') 'canary secret reached findings'
New-LdcTestState
function Invoke-LdcRequest { param($Platform,$Uri,$Method,$Body,[switch]$Raw) return @{Data=@{value=@(@{id=1;name='one'},@{id=2;name='two'})};Headers=@{}} }
$items=Get-LdcPaged -Platform AzureDevOps -Uri 'https://dev.azure.com/acme/proj/_apis/git/repositories?api-version=7.1'
Assert-Ldc ($items.Count -eq 2) 'ADO page items were not retained'

# Continuations are bounded and retain earlier observations when a later page fails.
New-LdcTestState
$script:PageCalls = 0
function script:Invoke-LdcRequest { param($Platform,$Uri,$Method,$Body,[switch]$Raw) $script:PageCalls++; if($script:PageCalls -eq 1){return @{Data=@{value=@(@{id='first';name='first'})};Headers=@{'x-ms-continuationtoken'='next'}}}; if($script:PageCalls -eq 2){return @{Data=@{value=@()};Headers=@{}}}; throw 'unexpected' }
$items=Get-LdcPaged AzureDevOps 'https://dev.azure.com/acme/proj/_apis/test?api-version=7.1'
Assert-Ldc (@($items).Count -eq 1) "continuation token did not preserve the first page (count=$(@($items).Count), calls=$script:PageCalls)"
New-LdcTestState
$script:PageCalls = 0
function script:Invoke-LdcRequest { param($Platform,$Uri,$Method,$Body,[switch]$Raw) $script:PageCalls++; if($script:PageCalls -eq 1){return @{Data=@{value=@(@{id='first';name='first'})};Headers=@{'x-ms-continuationtoken'='next'}}}; throw 'LDC:http-500' }
$items=Get-LdcPaged AzureDevOps 'https://dev.azure.com/acme/proj/_apis/test?api-version=7.1'
Assert-Ldc (@($items).Count -eq 1) 'later page failure discarded retained findings'
Assert-Ldc (($script:Ldc.Findings | Out-String) -match 'http-500') 'later page failure was not recorded as a coverage gap'
New-LdcTestState
$script:PageCalls = 0
function script:Invoke-LdcRequest { param($Platform,$Uri,$Method,$Body,[switch]$Raw) $script:PageCalls++; return @{Data=@{value=@(@{id='duplicate';name='duplicate'})};Headers=@{'x-ms-continuationtoken'='repeat'}} }
$items=Get-LdcPaged AzureDevOps 'https://dev.azure.com/acme/proj/_apis/test?api-version=7.1'
Assert-Ldc (($script:Ldc.Findings | Out-String) -match 'PAGINATION_DUPLICATE_OR_CYCLE') 'duplicate continuation item did not create a coverage gap'

# Content encoding is provider-aware: Azure plain content is never base64-decoded.
Assert-Ldc ((ConvertFrom-LdcContent @{Data=@{content='plain: value';encoding=''}} AzureDevOps) -eq 'plain: value') 'Azure plain content was decoded incorrectly'
Assert-Ldc ((ConvertFrom-LdcContent @{Data=@{content='cGxhaW46IHZhbHVl';encoding='base64'}} AzureDevOps) -eq 'plain: value') 'explicit Azure base64 content was not decoded'

# Static local references are followed at the same ref, and reporting remains redacted.
New-LdcTestState
$script:LocalFetches = 0
function Get-LdcGitHubText { param($Repository,$Path,$Ref) $script:LocalFetches++; if($Path -eq '.github/workflows/local.yml'){return 'LD_SDK_KEY: ld-secret-test'}; return $null }
Add-LdcTextReferences GitHub 'acme/demo' 'acme/demo/.github/workflows/root.yml@main' 'uses: ./.github/workflows/local.yml' 'acme/demo' '' 'acme/demo' @('acme/demo')
Assert-Ldc ($script:LocalFetches -eq 1) 'local reference was not followed at its current ref'
$safe=(Get-LdcReport | ConvertTo-Json -Depth 20)
Assert-Ldc ($safe -notmatch 'ld-secret-test') 'canary secret reached the report output'
Write-Host 'Repositories.Tests.ps1 passed'
