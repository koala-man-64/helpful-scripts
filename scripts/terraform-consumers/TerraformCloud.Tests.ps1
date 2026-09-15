# Offline integration fixtures. Real scope guard, matching, and report writer;
# only provider transport is replaced. No provider credentials or network used.
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/../launchdarkly-consumers/Core.ps1"
. "$PSScriptRoot/../launchdarkly-consumers/Configuration.ps1"
. "$PSScriptRoot/../launchdarkly-consumers/Repositories.ps1"
. "$PSScriptRoot/TerraformCloud.ps1"
function Assert-Tfc { param([bool]$Condition, [string]$Message) if (!$Condition) { throw "FAIL: $Message" } }
function Reset-Tfc {
    $config = @{
        terraformCloud = @{hostname = 'app.terraform.io'; organizations = @('fixture-org')}
        azure = @{subscriptionIds = @()}; gcp = @{projectIds = @()}
        github = @{organizations = @(); repositories = @('fixture/repo')}; azureDevOps = @()
    }
    Initialize-LdcState $config 'synthetic.atlasv1.knownCanary999' 'synthetic.management.Canary888' -CredentialKind TerraformCloud -From ([datetimeoffset]'2026-09-01T00:00:00Z') -To ([datetimeoffset]'2026-09-14T00:00:00Z')
    $script:Requests = [Collections.Generic.List[string]]::new()
}
function Invoke-LdcRequest {
    param($Platform, $Uri, $Method = 'GET', $Body, [switch]$Raw)
    Assert-LdcRequest $Platform ([uri]$Uri) $Method
    $script:Requests.Add("$Platform $Uri")
    & $script:Fixture ([uri]$Uri) $Platform
}
function New-TfcPage { param([array]$Items = @(), $Next = $null) @{Data = @{data = $Items; links = @{next = $Next}}; Headers = @{}} }
function New-TfcIdentity {
    @{Data = @{data = @{
        id = 'user-Synthetic'; 'type' = 'users'; attributes = @{'is-service-account' = $true}
        relationships = @{'authenticated-resource' = @{data = @{id = 'team-Owner'; 'type' = 'teams'}}}
        links = @{'auth-token' = '/api/v2/authentication-tokens/at-Known'}
    }}; Headers = @{}}
}
function Assert-TfcSafe {
    $json = Get-LdcReport | ConvertTo-Json -Depth 30
    Assert-Tfc ($json -notmatch 'synthetic\.atlasv1|synthetic\.management|different-credential-canary') 'credential leaked into report'
    Assert-Tfc ((Get-LdcReport).consumerCompleteness -eq 'not-established') 'consumer completeness became asserted'
}

# Known token identity is separate from an optional management token.
Reset-Tfc
Assert-Tfc ((Get-LdcToken TerraformIdentity) -ceq $script:Ldc.KnownSecret) 'identity uses wrong credential'
Assert-Tfc ((Get-LdcToken TerraformCloud) -ceq $script:Ldc.ManagementToken) 'inventory uses wrong credential'
$script:Fixture = { param($Uri, $Platform) New-TfcIdentity }
Invoke-LdcTerraformAccountIdentity
Assert-Tfc (@($script:Ldc.Findings | Where-Object { $_.Classification -eq 'AuthenticatedIdentity' -and $_.Resource -match 'team-Owner' }).Count -eq 1) 'underlying team identity was lost'
Assert-Tfc ($script:Requests.Count -eq 1 -and $script:Requests[0].StartsWith('TerraformIdentity ')) 'identity used unsupported token metadata endpoint'
Assert-TfcSafe

# All readable variables are compared; masked values remain gaps. Workspace and
# varset discovery authorizes only those IDs, and run activity is a separate class.
Reset-Tfc
$script:Fixture = {
    param($Uri, $Platform)
    switch ($Uri.AbsolutePath) {
        '/api/v2/account/details' { return New-TfcIdentity }
        '/api/v2/organizations/fixture-org/workspaces' {
            return New-TfcPage @(@{id = 'ws-App'; 'type' = 'workspaces'; attributes = @{name = 'app'; 'execution-mode' = 'remote'; 'working-directory' = 'infra'; 'vcs-repo' = @{identifier = 'fixture/repo'}}})
        }
        '/api/v2/workspaces/ws-App/vars' {
            return New-TfcPage @(
                @{id = 'var-Match'; 'type' = 'vars'; attributes = @{key = 'UNRELATED_NAME'; category = 'env'; sensitive = $false; value = 'synthetic.atlasv1.knownCanary999'}},
                @{id = 'var-Masked'; 'type' = 'vars'; attributes = @{key = 'TFE_TOKEN'; category = 'env'; sensitive = $true; value = $null}},
                @{id = 'var-Other'; 'type' = 'vars'; attributes = @{key = 'OTHER'; category = 'env'; sensitive = $false; value = 'different-credential-canary'}}
            )
        }
        '/api/v2/workspaces/ws-App/runs' {
            return New-TfcPage @(@{id = 'run-Recent'; 'type' = 'runs'; attributes = @{'created-at' = '2026-09-10T10:00:00Z'; status = 'applied'; source = 'tfe-api'}}, @{id = 'run-Old'; 'type' = 'runs'; attributes = @{'created-at' = '2020-01-01T00:00:00Z'; status = 'applied'}})
        }
        '/api/v2/organizations/fixture-org/varsets' {
            return New-TfcPage @(@{id = 'varset-Shared'; 'type' = 'varsets'; attributes = @{name = 'shared'; global = $false}; relationships = @{workspaces = @{data = @(@{id = 'ws-App'; 'type' = 'workspaces'})}; projects = @{data = @()}}})
        }
        '/api/v2/varsets/varset-Shared/relationships/vars' {
            return New-TfcPage @(@{id = 'var-Set'; 'type' = 'vars'; attributes = @{key = 'CI_TOKEN'; category = 'env'; sensitive = $false; value = 'synthetic.atlasv1.knownCanary999'}})
        }
        '/api/v2/varsets/varset-Shared/relationships/workspaces' { return New-TfcPage @(@{id = 'ws-App'; 'type' = 'workspaces'}) }
        '/api/v2/varsets/varset-Shared/relationships/projects' { return New-TfcPage @() }
        default { throw 'LDC:unexpected-fixture-request' }
    }
}
Invoke-LdcTerraformCloud
Assert-Tfc (@($script:Ldc.Findings | Where-Object Classification -eq ExactStoredKeyMatch).Count -eq 2) 'workspace or variable-set exact match missing'
Assert-Tfc (@($script:Ldc.Findings | Where-Object Classification -eq WorkspaceActivity).Count -eq 1) 'run observation window was not respected'
Assert-Tfc (@($script:Ldc.Findings | Where-Object { $_.Resource -eq 'var-Masked' -and $_.Classification -eq 'CoverageGap' }).Count -gt 0) 'sensitive variable lost its gap'
Assert-Tfc ($script:Ldc.TerraformWorkspaces.Contains('ws-App') -and !$script:Ldc.TerraformWorkspaces.Contains('ws-Other')) 'workspace scope registration is incorrect'
Assert-TfcSafe

# The documented workspace-variable endpoint may omit pagination entirely.
Reset-Tfc
[void]$script:Ldc.TerraformWorkspaces.Add('ws-Unpaginated')
$script:Fixture = {
    param($Uri, $Platform)
    @{Data = @{data = @(@{id = 'var-Plain'; 'type' = 'vars'; attributes = @{key = 'TFE_TOKEN'; category = 'env'; sensitive = $false; value = 'synthetic.atlasv1.knownCanary999'}})}; Headers = @{}}
}
Invoke-LdcScope TerraformCloud fixture { Invoke-LdcTerraformWorkspaceVariables 'fixture-org' 'ws-Unpaginated' }
Assert-Tfc (@($script:Ldc.Findings | Where-Object Classification -eq ExactStoredKeyMatch).Count -eq 1) 'unpaginated variable match lost'
Assert-Tfc ($script:Ldc.GapCount -eq 0 -and $script:Ldc.Coverage[0].Status -eq 'scanned') 'documented unpaginated response became a false gap'

# A later workspace page failure retains earlier inventory and independent varsets.
Reset-Tfc
$script:Fixture = {
    param($Uri, $Platform)
    if ($Uri.AbsolutePath -eq '/api/v2/account/details') { return New-TfcIdentity }
    if ($Uri.AbsolutePath -eq '/api/v2/organizations/fixture-org/workspaces') {
        if ($Uri.Query) { throw 'LDC:http-403' }
        return New-TfcPage @(@{id = 'ws-Retained'; 'type' = 'workspaces'; attributes = @{name = 'retained'}}) '/api/v2/organizations/fixture-org/workspaces?page%5Bnumber%5D=2'
    }
    return New-TfcPage @()
}
Invoke-LdcTerraformCloud
Assert-Tfc (@($script:Ldc.Findings | Where-Object { $_.Resource -eq 'ws-Retained' -and $_.Classification -eq 'WorkspaceInventory' }).Count -eq 1) 'later page failure discarded earlier workspace'
Assert-Tfc (@($script:Requests | Where-Object { $_ -match '/organizations/fixture-org/varsets' }).Count -eq 1) 'workspace error suppressed variable-set inventory'
Assert-Tfc ((Get-LdcReport).status -eq 'incomplete') 'partial inventory became complete'

# Scope-bound GET only, selected host only, discovered children only.
Reset-Tfc
foreach ($case in @(
    @('TerraformCloud','https://app.terraform.io/api/v2/organizations/other/workspaces','GET'),
    @('TerraformCloud','https://app.terraform.io/api/v2/workspaces/ws-Unknown/vars','GET'),
    @('TerraformCloud','https://app.eu.terraform.io/api/v2/organizations/fixture-org/workspaces','GET'),
    @('TerraformIdentity','https://app.terraform.io/api/v2/account/details','POST'),
    @('TerraformIdentity','https://evil.example/api/v2/account/details','GET')
)) {
    $denied = $false
    try { Assert-LdcRequest $case[0] ([uri]$case[1]) $case[2] } catch { $denied = $true }
    Assert-Tfc $denied 'unsafe or off-scope Terraform request permitted'
}

# Terraform source patterns preserve names, exact case, and full token boundaries.
Reset-Tfc
Add-LdcTextReferences GitHub 'fixture/repo' 'infra/main.tf' 'TFE_TOKEN=synthetic.atlasv1.knownCanary999' 'fixture/repo' '' 'fixture/repo' @('fixture/repo')
Add-LdcTextReferences GitHub 'fixture/repo' 'infra/not-match.tf' 'token=synthetic.atlasv1.knownCanary999.longer' 'fixture/repo' '' 'fixture/repo' @('fixture/repo')
Assert-Tfc (@($script:Ldc.Findings | Where-Object Classification -eq ExactSourceKeyMatch).Count -eq 1) 'Terraform exact source boundary incorrect'
Assert-Tfc (@($script:Ldc.Findings | Where-Object SecretReference -eq TFE_TOKEN).Count -gt 0) 'Terraform token variable reference not inventoried'
Assert-TfcSafe

# Report files are Terraform-named, safe, and never overwrite existing reports.
Reset-Tfc
$script:Fixture = { param($Uri, $Platform) New-TfcPage @() }
Invoke-LdcTerraformVariableSet 'fixture-org' @{id = 'varset-Unverified'; attributes = @{name = 'shared'; global = 'true'}; relationships = @{workspaces = @{data = @(@{id = 'ws-Hidden'; 'type' = 'workspaces'})}; projects = @{data = @()}}}
Assert-Tfc (@($script:Ldc.Findings | Where-Object { $_.Resource -eq 'ws-Hidden' -and $_.Classification -eq 'UnresolvedCandidate' }).Count -eq 1) 'uninventoried workspace reference was lost'
Assert-Tfc (@($script:Ldc.Findings | Where-Object Classification -eq VariableSetGlobalBinding).Count -eq 0) 'malformed global flag created a global binding claim'
Assert-Tfc (!$script:Ldc.TerraformWorkspaces.Contains('ws-Hidden')) 'variable-set reference expanded workspace read scope'
Add-LdcTerraformVariableFinding 'fixture-org' 'ws-Hidden' 'var-Sensitive' @{attributes = @{key = 'different-credential-canary'; value = 'different-credential-canary'; sensitive = $true; category = 'env'}}
Assert-TfcSafe

Reset-Tfc
[void]$script:Ldc.TerraformVariableSets.Add('varset-Allowed')
$denied = $false
try { Assert-LdcRequest TerraformCloud ([uri]'https://app.terraform.io/api/v2/varsets/varset-Allowed/relationships/workspaces') GET } catch { $denied = $true }
Assert-Tfc $denied 'unsupported relationship route permitted'

# Optional token metadata must never suppress verified account identity.
$script:Fixture = {
    param($Uri, $Platform)
    $identity = New-TfcIdentity
    $identity.Data.data.links['auth-token'] = 'https://evil.example/api/v2/authentication-tokens/at-False'
    return $identity
}
Invoke-LdcTerraformAccountIdentity
Assert-Tfc (@($script:Ldc.Findings | Where-Object Classification -eq AuthenticatedIdentity).Count -eq 1) 'invalid metadata link discarded identity'
Assert-Tfc ($script:Ldc.GapCount -gt 0 -and $script:Requests.Count -eq 1) 'untrusted metadata link was followed or ignored without gap'
Reset-Tfc
$script:Fixture = {
    param($Uri, $Platform)
    @{Data = @{data = @{id = 'user-Human'; 'type' = 'users'; attributes = @{'is-service-account' = $false}; links = @{}}}; Headers = @{}}
}
Invoke-LdcTerraformAccountIdentity
Assert-Tfc (@($script:Ldc.Findings | Where-Object { $_.Classification -eq 'AuthenticatedIdentity' -and $_.Resource -eq 'users/user-Human' }).Count -eq 1) 'documented user identity fallback lost'

# Pagination bounds and hostile links fail closed after preserving first items.
foreach ($fixtureMode in @('offhost','other-collection','duplicate','limit','malformed')) {
    Reset-Tfc
    $script:FixtureMode = $fixtureMode
    if ($fixtureMode -eq 'limit') { $script:Ldc.MaxPages = 1 }
    $script:Fixture = {
        param($Uri, $Platform)
        if ($script:FixtureMode -eq 'malformed') { return @{Data = @{data = @{id = 'ws-Bad'}}; Headers = @{}} }
        $next = switch ($script:FixtureMode) {
            'offhost' { 'https://evil.example/api/v2/organizations/fixture-org/workspaces?page=2' }
            'other-collection' { '/api/v2/organizations/fixture-org/varsets?page=2' }
            default { '/api/v2/organizations/fixture-org/workspaces?page=2' }
        }
        New-TfcPage @(@{id = 'ws-One'; 'type' = 'workspaces'; attributes = @{name = 'one'}}) $next
    }
    $script:ItemsSeen = 0; $denied = $false
    try { Invoke-LdcTerraformPages 'fixture-org' '/api/v2/organizations/fixture-org/workspaces' workspaces { param($item) $script:ItemsSeen++ } } catch { $denied = $true }
    Assert-Tfc $denied 'invalid pagination did not stop'
    Assert-Tfc ($script:ItemsSeen -eq $(if ($fixtureMode -eq 'malformed') { 0 } else { 1 })) 'pagination lost or duplicated accepted observations'
}
Reset-Tfc
Add-LdcFinding -Platform TerraformCloud -Scope fixture -Resource 'synthetic.atlasv1.knownCanary999' -Classification ExactStoredKeyMatch
$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('tfc-tests-' + [guid]::NewGuid().ToString('N'))
try {
    [void](Write-LdcReport $tempRoot)
    foreach ($file in @('terraform-cloud-consumers.json','terraform-cloud-consumers.csv')) {
        $text = [IO.File]::ReadAllText((Join-Path $tempRoot $file))
        Assert-Tfc ($text -notmatch 'synthetic\.atlasv1|synthetic\.management') 'credential leaked to report file'
    }
    $denied = $false
    try { Write-LdcReport $tempRoot | Out-Null } catch { $denied = $true }
    Assert-Tfc $denied 'report overwrite permitted'
    $configPath = Join-Path $tempRoot 'scope.json'
    [IO.File]::WriteAllText($configPath, '{"terraformCloud":{"organizations":["fixture-org"]}}')
    $config = Read-LdcConfiguration $configPath TerraformCloud
    Assert-Tfc ($config.terraformCloud.hostname -eq 'app.terraform.io') 'default Terraform hostname incorrect'
    [IO.File]::WriteAllText($configPath, '{"terraformCloud":{"hostname":"evil.example","organizations":["fixture-org"]}}')
    $denied = $false
    try { Read-LdcConfiguration $configPath TerraformCloud | Out-Null } catch { $denied = $true }
    Assert-Tfc $denied 'unapproved API host permitted'
} finally {
    # Only these test-owned files can be removed; there is no recursive deletion.
    foreach ($name in @('scope.json','terraform-cloud-consumers.json','terraform-cloud-consumers.csv')) {
        $path = Join-Path $tempRoot $name
        if ([IO.File]::Exists($path)) { [IO.File]::Delete($path) }
    }
    if ([IO.Directory]::Exists($tempRoot)) { [IO.Directory]::Delete($tempRoot) }
}
Write-Host 'Terraform Cloud offline assertions passed.'
