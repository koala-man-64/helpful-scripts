# Native PowerShell 7 tests: real state, matching, scope guard, and report helpers.
# Only the provider transport is replaced; fixtures contain no real credentials.
$ErrorActionPreference='Stop'
. "$PSScriptRoot/Core.ps1"
. "$PSScriptRoot/Cloud.ps1"
$script:Assertions=0
$script:Subscription='00000000-0000-0000-0000-000000000001'
$script:VaultId="/subscriptions/$script:Subscription/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/vault-a"
$script:Base='https://vault-a.vault.azure.net/secrets/billing-password'
function Assert-Cloud([bool]$Condition,[string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
    $script:Assertions++
}
function Reset-Cloud([switch]$Gcp) {
    $config=@{azure=@{subscriptionIds=@($script:Subscription)};gcp=@{projectIds=@()};github=@{organizations=@();repositories=@()};azureDevOps=@()}
    if ($Gcp) { $config.azure.subscriptionIds=@(); $config.gcp.projectIds=@('test-project') }
    Initialize-LdcState $config 'sdk-cloud-fixture-canary' 'management-fixture-canary'
    $script:Responses=[Collections.Generic.Queue[object]]::new()
    $script:Requests=[Collections.Generic.List[string]]::new()
}
function Add-CloudResponse([string]$Pattern,[hashtable]$Data,[string]$Failure='') {
    $script:Responses.Enqueue(@{Pattern=$Pattern;Data=$Data;Failure=$Failure})
}
function Invoke-LdcRequest {
    param([string]$Platform,[string]$Uri,[string]$Subscription)
    Assert-LdcRequest $Platform ([uri]$Uri) GET
    $script:Requests.Add($Uri)
    if (-not $script:Responses.Count) { throw 'LDC:fixture-exhausted' }
    $response=$script:Responses.Dequeue()
    if ($Uri -notmatch $response.Pattern) { throw 'LDC:fixture-route-mismatch' }
    if ($response.Failure) { throw "LDC:$($response.Failure)" }
    return @{Data=$response.Data;Headers=@{}}
}
function Assert-CloudConsumed {
    Assert-Cloud ($script:Responses.Count -eq 0) 'all planned requests executed'
    Assert-Cloud (-not (($script:Ldc.Findings | ConvertTo-Json -Depth 10) -match 'fixture-route-mismatch|fixture-exhausted')) 'no unexpected transport route'
}

# Generic Azure inventory often omits vault properties. Resolve metadata, then
# scan all versions under a completely unrelated secret name from an empty allowlist.
Reset-Cloud
Add-CloudResponse '/resources\?' @{value=@(@{id=$script:VaultId;'type'='Microsoft.KeyVault/vaults';location='eastus'})}
Add-CloudResponse '/vaults/vault-a\?' @{id=$script:VaultId;properties=@{vaultUri='https://vault-a.vault.azure.net/'};location='eastus';tags=@{owner='ops';unrelated='not-exported'}}
Add-CloudResponse '/secrets\?' @{value=@(@{id=$script:Base})}
Add-CloudResponse '/versions\?' @{value=@(
    @{id="$script:Base/aaa";attributes=@{enabled=$true}},
    @{id="$script:Base/bbb";attributes=@{enabled=$false}},
    @{id="$script:Base/ccc";attributes=@{enabled=$true}},
    @{id="$script:Base/ddd";attributes=@{enabled=$true}},
    @{id="$script:Base/eee";attributes=@{enabled=$true}}
)}
Add-CloudResponse '/aaa\?' @{id="$script:Base/aaa";value='sdk-cloud-fixture-canary'}
Add-CloudResponse '/ccc\?' @{} 'http-403'
Add-CloudResponse '/ddd\?' @{id="$script:Base/ddd";value='unrelated-value-canary'}
Add-CloudResponse '/eee\?' @{id="$script:Base/eee";value='sdk-cloud-fixture-canary'}
Invoke-LdcCloud
Assert-CloudConsumed
$report=Get-LdcReport
$matches=@($report.findings | Where-Object Classification -eq ExactStoredKeyMatch)
Assert-Cloud ($matches.Count -eq 2) 'old and later readable versions both match'
Assert-Cloud ($matches[0].SecretReference -ceq $script:Base -and $matches[0].SecretVersion -ceq 'aaa') 'canonical Azure reference and pinned version'
Assert-Cloud ($script:Ldc.AllowedVaultHosts.Contains('vault-a.vault.azure.net')) 'verified discovered vault is automatically permitted'
Assert-Cloud ($report.status -eq 'incomplete') 'disabled and denied versions keep scan incomplete'
Assert-Cloud (@($report.findings | Where-Object Evidence -eq 'http-403').Count -eq 1) 'permission failure category retained'
Assert-Cloud (-not (($report | ConvertTo-Json -Depth 20) -match 'sdk-cloud-fixture-canary|management-fixture-canary|unrelated-value-canary|not-exported')) 'no values or arbitrary labels in report'

Reset-Cloud
Add-CloudResponse '/resources\?' @{value=@(@{id=$script:VaultId;'type'='Microsoft.KeyVault/vaults';properties=@{vaultUri='https://outside.vault.azure.net/'}})}
Invoke-LdcCloud
Assert-CloudConsumed
Assert-Cloud ($script:Requests.Count -eq 1 -and $script:Ldc.AllowedVaultHosts.Count -eq 0) 'mismatched vault host never receives a request'

Reset-Cloud
$start="https://management.azure.com/subscriptions/$script:Subscription/resources?api-version=2021-04-01"
Add-CloudResponse '/resources\?' @{value=@(@{id="/subscriptions/$script:Subscription/resourceGroups/rg/providers/Microsoft.Web/sites/app";'type'='Microsoft.Web/sites';location='westus'});nextLink=($start+'&page=2')}
Add-CloudResponse 'page=2' @{} 'http-500'
Invoke-LdcCloud
Assert-CloudConsumed
Assert-Cloud (@($script:Ldc.Findings | Where-Object Classification -eq ResourceInventory).Count -eq 1) 'later inventory failure retains earlier resources'
Assert-Cloud ((Get-LdcReport).status -eq 'incomplete') 'later inventory failure is not complete'

foreach ($next in @('https://evil.example/resources',$start,($start -replace '/resources\?','/resources-other?'))) {
    Reset-Cloud
    Add-CloudResponse '/resources\?' @{value=@();nextLink=$next}
    Invoke-LdcCloud
    Assert-CloudConsumed
    Assert-Cloud ($script:Requests.Count -eq 1 -and $script:Ldc.GapCount -gt 0) 'cycle, off-host, or different collection continuation rejected'
}
Reset-Cloud
Add-CloudResponse '/resources\?' @{}
Invoke-LdcCloud
Assert-Cloud ((Get-LdcReport).status -eq 'incomplete') 'missing Azure collection is not an empty scan'

# GCP list APIs legitimately omit empty repeated fields. Read versions using
# verified project-number aliases; reject another secret/project and invalid UTF-8.
Reset-Cloud -Gcp
Add-CloudResponse '/v3/projects/test-project$' @{name='projects/123456789';projectId='test-project'}
Add-CloudResponse ':searchAllResources$' @{results=@(@{name='//run.googleapis.com/projects/123456789/locations/us-central1/services/app';assetType='run.googleapis.com/Service';location='us-central1'})}
Add-CloudResponse '/projects/test-project/secrets$' @{secrets=@(@{name='projects/123456789/secrets/UpperCase';labels=@{team='data'}});nextPageToken='second'}
Add-CloudResponse '/UpperCase/versions$' @{versions=@(
    @{name='projects/123456789/secrets/UpperCase/versions/1';state='ENABLED'},
    @{name='projects/999999999/secrets/UpperCase/versions/2';state='ENABLED'},
    @{name='projects/123456789/secrets/UpperCase/versions/3';state='DISABLED'},
    @{name='projects/123456789/secrets/UpperCase/versions/4';state='ENABLED'},
    @{name='projects/123456789/secrets/UpperCase/versions/5';state='ENABLED'}
)}
Add-CloudResponse '/versions/1:access$' @{name='projects/123456789/secrets/UpperCase/versions/1';payload=@{data=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('sdk-cloud-fixture-canary'))}}
Add-CloudResponse '/versions/4:access$' @{} 'http-403'
Add-CloudResponse '/versions/5:access$' @{name='projects/123456789/secrets/UpperCase/versions/5';payload=@{data='/w=='}}
Add-CloudResponse 'pageToken=second' @{}
Invoke-LdcCloud
Assert-CloudConsumed
$report=Get-LdcReport
$matches=@($report.findings | Where-Object Classification -eq ExactStoredKeyMatch)
Assert-Cloud ($matches.Count -eq 1 -and $matches[0].SecretReference -ceq 'projects/123456789/secrets/UpperCase' -and $matches[0].SecretVersion -ceq '1') 'GCP exact decoded comparison and canonical version'
Assert-Cloud (@($report.findings | Where-Object Evidence -eq CrossScopeSecretVersion).Count -eq 1) 'cross-project version rejected before access'
Assert-Cloud (@($report.findings | Where-Object Evidence -eq SecretPayloadNotUtf8).Count -eq 1) 'invalid UTF-8 is an explicit gap'
Assert-Cloud (@($script:Requests | Where-Object { $_ -match 'pageToken=second' }).Count -eq 1) 'GCP continuation token consumed'
Assert-Cloud (($report | ConvertTo-Json -Depth 20) -notmatch 'sdk-cloud-fixture-canary') 'decoded canary is absent from report'

Reset-Cloud -Gcp
$script:Ldc.Config.gcp.projectIds=@('123456789')
Add-CloudResponse '/v3/projects/123456789$' @{name='projects/123456789';projectId='test-project'}
Add-CloudResponse ':searchAllResources$' @{results=@(@{name='//secretmanager.googleapis.com/projects/123456789/locations/us-central1/secrets/regional';assetType='secretmanager.googleapis.com/Secret';location='us-central1'})}
Add-CloudResponse '/projects/123456789/secrets$' @{}
Invoke-LdcCloud
Assert-CloudConsumed
Assert-Cloud ($script:Ldc.GcpProjectAliases['123456789'] -eq '123456789') 'explicit numeric project scope resolves'
Assert-Cloud ((Get-LdcReport).scopeSummary.unsupported -eq 1) 'regional resource recorded as unsupported'
Write-Output 'Cloud real-Core offline assertions passed.'
