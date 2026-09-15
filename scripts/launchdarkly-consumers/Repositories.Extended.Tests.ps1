# Integration fixtures use the real scope guard, matching, and report sanitization.
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/Core.ps1"
. "$PSScriptRoot/Repositories.ps1"
function Assert-Extended { param([bool]$Condition, [string]$Message) if (!$Condition) { throw "Assertion failed: $Message" } }
function Reset-Extended {
    Initialize-LdcState -Config @{
        azure = @{subscriptionIds = @()}; gcp = @{projectIds = @()}
        github = @{organizations = @('acme'); repositories = @()}
        azureDevOps = @(@{organization = 'acme'; projects = @('proj')})
    } -KnownSecret 'synthetic-sdk-credential-canary-4321' -ManagementToken 'synthetic-management-canary-9876'
    $script:Requests = [Collections.Generic.List[string]]::new()
}
function Invoke-LdcRequest {
    param($Platform, $Uri, $Method = 'GET', $Body, [switch]$Raw)
    Assert-LdcRequest $Platform ([uri]$Uri) $Method
    $script:Requests.Add($Uri)
    & $script:Fixture ([uri]$Uri)
}
function Assert-SafeExtended {
    $report = Get-LdcReport | ConvertTo-Json -Depth 30
    foreach ($value in @('synthetic-sdk-credential-canary-4321', 'synthetic-management-canary-9876', 'other-readable-canary-7777')) {
        Assert-Extended (!$report.Contains($value)) 'credential escaped into a serialized report'
    }
    Assert-Extended ($report.Contains('not-established')) 'runtime completeness became asserted'
}

# A variable group can contain readable and masked variables plus a Key Vault mapping.
Reset-Extended
$script:Fixture = {
    param($Uri)
    @{Data = @{
        id = 12; name = 'group'; 'type' = 'Vsts'
        variables = @{
            MATCHED = @{value = 'synthetic-sdk-credential-canary-4321'; isSecret = $false}
            MASKED = @{value = $null; isSecret = $true}
            DIFFERENT = @{value = 'other-readable-canary-7777'; isSecret = $false}
        }
    }; Headers = @{}}
}
Invoke-LdcAdoVariableGroup 'https://dev.azure.com/acme/proj/_apis' 'acme/proj' @{id = 12; name = 'group'}
Assert-Extended (@($script:Ldc.Findings | Where-Object { $_.Classification -match '^Exact' }).Count -gt 0) 'readable matching variable was not attributed'
Assert-Extended (@($script:Ldc.Findings | Where-Object { $_.Classification -eq 'CoverageGap' }).Count -gt 0) 'masked variable was silently treated as covered'
Assert-SafeExtended

Reset-Extended
$script:Fixture = {
    param($Uri)
    @{Data = @{
        id = 13; name = 'vault-group'; 'type' = 'AzureKeyVault'
        providerData = @{vault = 'fixture-vault'; serviceEndpointId = 'fixture-connection'}
        variables = @{MASKED = @{value = $null; isSecret = $true}}
    }; Headers = @{}}
}
Invoke-LdcAdoVariableGroup 'https://dev.azure.com/acme/proj/_apis' 'acme/proj' @{id = 13; name = 'vault-group'}
Assert-Extended (@($script:Ldc.Findings | Where-Object { $_.SecretReference -eq 'https://fixture-vault.vault.azure.net/secrets/masked' }).Count -gt 0) 'Key Vault variable mapping lacks its canonical secret reference'

# Default-branch inventory reaches ordinary source files and nested local templates.
Reset-Extended
$script:Fixture = {
    param($Uri)
    if ($Uri.Query -match 'recursionLevel=') {
        return @{Data = @{value = @(
            @{path = '/src/app.go'; gitObjectType = 'blob'; isFolder = $false},
            @{path = '/pipelines/root.yml'; gitObjectType = 'blob'; isFolder = $false}
        )}; Headers = @{}}
    }
    $q = [uri]::UnescapeDataString($Uri.Query)
    if ($q -match 'path=/src/app.go(?:&|$)') { return @{Data = @{content = 'LaunchDarkly: synthetic-sdk-credential-canary-4321'}; Headers = @{}} }
    if ($q -match 'path=/pipelines/root.yml(?:&|$)') { return @{Data = @{content = "steps:`n- template: nested.yml"}; Headers = @{}} }
    if ($q -match 'path=/pipelines/nested.yml(?:&|$)') { return @{Data = @{content = 'LD: https://fixture-vault.vault.azure.net/secrets/arbitrary/old123'}; Headers = @{}} }
    throw 'LDC:fixture-unexpected-request'
}
Invoke-LdcAdoRepository 'https://dev.azure.com/acme/proj/_apis' 'acme/proj' @{id = 'repo-id'; name = 'app'; defaultBranch = 'refs/heads/main'}
Assert-Extended (@($script:Ldc.Findings | Where-Object { $_.Classification -eq 'ExactSourceKeyMatch' }).Count -gt 0) 'default-branch source inventory missed an ordinary Go file'
Assert-Extended (@($script:Ldc.Findings | Where-Object { $_.SecretReference -eq 'https://fixture-vault.vault.azure.net/secrets/arbitrary' -and $_.SecretVersion -eq 'old123' }).Count -gt 0) 'local template reference was not followed'
Assert-Extended (@($script:Requests | Where-Object { $_ -match 'versionDescriptor.version=refs' }).Count -eq 0) 'branch API received an unnormalized refs/heads prefix'
Assert-SafeExtended

# Collection shape and continuation validation fail closed without losing earlier items.
Reset-Extended
$script:Fixture = { param($Uri) @{Data = @{unexpected = 'shape'}; Headers = @{}} }
$items = @(Get-LdcPaged AzureDevOps 'https://dev.azure.com/acme/proj/_apis/git/repositories?api-version=7.1')
Assert-Extended ($items.Count -eq 0 -and $script:Ldc.GapCount -gt 0) 'malformed collection became an inventory item'
Reset-Extended
$script:Fixture = { param($Uri) @{Data = @(@{id = 1; name = 'one'}); Headers = @{Link = '<https://api.github.com/orgs/acme/actions/secrets?page=2>; rel="next"'}} }
$items = @(Get-LdcPaged GitHub 'https://api.github.com/orgs/acme/repos')
Assert-Extended ($items.Count -eq 1 -and $script:Requests.Count -eq 1 -and $script:Ldc.GapCount -gt 0) 'continuation switched to another collection'

# Static GitHub secret expressions retain the name without implying value access.
Reset-Extended
Add-LdcTextReferences GitHub 'acme/app' 'acme/app/.github/workflows/main.yml@main' 'LD: ${{ secrets.LAUNCHDARKLY }}' 'acme/app' '' 'acme/app' @('acme/app')
Assert-Extended (@($script:Ldc.Findings | Where-Object { $_.SecretReference -match 'LAUNCHDARKLY' -and $_.Classification -eq 'UnresolvedCandidate' }).Count -gt 0) 'static GitHub secret reference name was lost'
Assert-SafeExtended
# Cross-project aliases honor their declared project and tag; an unscoped alias is not fetched.
Reset-Extended
$script:Ldc.Config.azureDevOps[0].projects += 'shared'
$script:Fixture = {
    param($Uri)
    if ($Uri.AbsolutePath -eq '/acme/proj/_apis/git/repositories/app/items') {
        return @{Data = @{content = @'
resources:
  repositories:
  - repository: common
    type: git
    name: shared/templates
    ref: refs/tags/v1
  - repository: forbidden
    type: git
    name: outside/templates
steps:
- template: /steps.yml@common
- template: /steps.yml@forbidden
'@}; Headers = @{}}
    }
    if ($Uri.AbsolutePath -eq '/acme/shared/_apis/git/repositories/templates/items') {
        return @{Data = @{content = 'LD: projects/fixture/secrets/CaseName/versions/8'}; Headers = @{}}
    }
    throw 'LDC:fixture-unexpected-request'
}
Invoke-LdcAdoTextReferences 'https://dev.azure.com/acme/proj/_apis' 'acme/proj' 'app' '/pipeline.yml' 'main' 'app'
Assert-Extended (@($script:Requests | Where-Object { $_ -match '/acme/shared/.*versionDescriptor.version=v1&versionDescriptor.versionType=tag' }).Count -eq 1) 'cross-project alias lost its project or pinned tag'
Assert-Extended (@($script:Requests | Where-Object { $_ -match '/acme/outside/' }).Count -eq 0) 'off-scope alias was requested'
Assert-Extended (@($script:Ldc.Findings | Where-Object { $_.Evidence -eq 'OFF_SCOPE_REFERENCE_NOT_FOLLOWED' }).Count -eq 1) 'off-scope alias did not preserve a gap'

# Release environments retain their variables and task configuration references.
Reset-Extended
$script:Fixture = {
    param($Uri)
    @{Data = @{id = 20; name = 'deploy'; variables = @{}; environments = @(@{
        name = 'production'
        variables = @{HIDDEN = @{isSecret = $true; value = $null}; KEY = @{value = 'synthetic-sdk-credential-canary-4321'}}
        deployPhases = @(@{workflowTasks = @(@{name = 'configure'; inputs = @{setting = 'LD=https://fixture-vault.vault.azure.net/secrets/arbitrary/old123'}})})
    })}; Headers = @{}}
}
Invoke-LdcAdoRelease 'acme' 'proj' 'acme/proj' @{id = 20; name = 'deploy'}
Assert-Extended (@($script:Ldc.Findings | Where-Object { $_.Classification -eq 'ExactStoredKeyMatch' -and $_.Environment -eq 'production' }).Count -gt 0) 'environment variable match was lost'
Assert-Extended (@($script:Ldc.Findings | Where-Object { $_.SecretVersion -eq 'old123' -and $_.Environment -eq 'production' }).Count -gt 0) 'deployment task reference was lost'
Assert-SafeExtended

# Organization metadata records all/private visibility without calling the selected-only API.
Reset-Extended
$script:Fixture = {
    param($Uri)
    if ($Uri.AbsolutePath -eq '/orgs/acme/actions/secrets') {
        return @{Data = @{secrets = @(@{name = 'ALL'; visibility = 'all'}, @{name = 'SELECTED'; visibility = 'selected'})}; Headers = @{}}
    }
    if ($Uri.AbsolutePath -eq '/orgs/acme/actions/secrets/SELECTED/repositories') {
        return @{Data = @{repositories = @(@{id = 1; full_name = 'acme/app'})}; Headers = @{}}
    }
    throw 'LDC:fixture-unexpected-request'
}
Invoke-LdcGitHubOrganizationSecrets 'acme'
Assert-Extended ($script:Requests.Count -eq 2) 'organization visibility used an invalid selected-repository lookup'
Assert-Extended (@($script:Ldc.Findings | Where-Object { $_.Classification -eq 'SecretScope' -and $_.Resource -match 'acme/app' }).Count -eq 1) 'selected repository scope was lost'
Write-Host 'Repositories.Extended.Tests.ps1 passed'
