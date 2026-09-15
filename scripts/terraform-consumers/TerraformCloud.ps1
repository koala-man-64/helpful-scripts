# Terraform Cloud collector.  This file deliberately contains no import-time work.

function Test-LdcTerraformId {
    param([string]$Id, [ValidateSet('workspace','varset','variable','run','project','account')][string]$Kind)
    $pattern = switch ($Kind) {
        'workspace' { '^ws-[A-Za-z0-9]+$' }
        'varset'    { '^varset-[A-Za-z0-9]+$' }
        'variable'  { '^var-[A-Za-z0-9]+$' }
        'run'       { '^run-[A-Za-z0-9]+$' }
        'project'   { '^prj-[A-Za-z0-9]+$' }
        'account'   { '^[A-Za-z][A-Za-z0-9-]*$' }
    }
    return $Id -cmatch $pattern
}

function Get-LdcTerraformUri {
    param([string]$Path)
    $hostname = [string]$script:Ldc.Config.terraformCloud.hostname
    if ($hostname -notin @('app.terraform.io', 'app.eu.terraform.io')) { throw 'LDC:invalid-terraform-hostname' }
    if (-not $Path.StartsWith('/api/v2/', [StringComparison]::Ordinal)) { throw 'LDC:terraform-path-outside-api' }
    return "https://$hostname$Path"
}

function Get-LdcTerraformLinkUri {
    param([string]$Link, [string]$CollectionPath)
    if (-not $Link) { return $null }
    $base = [uri](Get-LdcTerraformUri $CollectionPath)
    try { $next = [uri]::new($base, $Link) } catch { throw 'LDC:invalid-terraform-pagination-link' }
    if (-not $next.IsAbsoluteUri -or $next.Scheme -cne 'https' -or
        $next.Host -cne $base.Host -or $next.Port -ne $base.Port -or
        $next.AbsolutePath -cne $CollectionPath) { throw 'LDC:terraform-pagination-outside-collection' }
    return $next.AbsoluteUri
}

function Invoke-LdcTerraformPages {
    param(
        [string]$Scope,
        [string]$CollectionPath,
        [string]$ExpectedType,
        [scriptblock]$OnItem,
        [switch]$AllowUnpaginated
    )
    $uri = Get-LdcTerraformUri $CollectionPath
    $seenLinks = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $seenItems = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    for ($page = 0; $page -lt $script:Ldc.MaxPages; $page++) {
        if (-not $seenLinks.Add($uri)) { throw 'LDC:terraform-pagination-cycle' }
        $document = (Invoke-LdcRequest -Platform TerraformCloud -Uri $uri).Data
        if ($document -isnot [Collections.IDictionary] -or $document.data -isnot [array]) {
            throw 'LDC:invalid-terraform-collection'
        }
        foreach ($item in $document.data) {
            if ($item -isnot [Collections.IDictionary] -or [string]$item.type -cne $ExpectedType -or -not [string]$item.id) {
                throw 'LDC:invalid-terraform-collection-item'
            }
            if (-not $seenItems.Add([string]$item.id)) { throw 'LDC:duplicate-terraform-collection-item' }
            & $OnItem $item
        }
        # The workspace-variable list is documented as an unpaginated data-only
        # response. Other collections must retain their pagination contract.
        if ($AllowUnpaginated -and -not $document.Contains('links') -and -not $document.Contains('meta')) { return }
        if (-not $document.Contains('links') -or $document.links -isnot [Collections.IDictionary] -or
            -not $document.links.Contains('next')) { throw 'LDC:missing-terraform-pagination-link' }
        $nextPage = $null
        if ($document.meta -is [Collections.IDictionary] -and $document.meta.pagination -is [Collections.IDictionary] -and
            $document.meta.pagination.Contains('next-page')) { $nextPage = $document.meta.pagination['next-page'] }
        if ($null -ne $nextPage -and $document.links.next -isnot [string]) { throw 'LDC:terraform-pagination-contradiction' }
        if ($null -eq $nextPage -and $document.meta -is [Collections.IDictionary] -and $document.meta.pagination -is [Collections.IDictionary] -and
            $document.meta.pagination.Contains('next-page') -and $null -ne $document.links.next) { throw 'LDC:terraform-pagination-contradiction' }
        if ($null -eq $document.links.next) { return }
        if ($document.links.next -isnot [string]) { throw 'LDC:invalid-terraform-pagination-link' }
        $uri = Get-LdcTerraformLinkUri $document.links.next $CollectionPath
    }
    throw 'LDC:terraform-pagination-limit'
}

function Add-LdcTerraformVariableFinding {
    param([string]$Scope, [string]$OwnerId, [string]$VariableId, $Variable)
    if (-not (Test-LdcTerraformId $VariableId variable) -or $Variable -isnot [Collections.IDictionary] -or
        $Variable.attributes -isnot [Collections.IDictionary] -or -not $Variable.attributes.Contains('key')) {
        Add-LdcGap -Platform TerraformCloud -Scope $Scope -Resource $OwnerId -Reason 'MalformedTerraformVariable'
        return
    }
    $attributes = $Variable.attributes
    # Register any returned payload before projecting metadata, even if an
    # unexpected response marks a non-null value as sensitive.
    if ($attributes.value -is [string]) { Register-LdcSensitive $attributes.value }
    $key = Protect-LdcText ([string]$attributes.key)
    $category = Protect-LdcText ([string]$attributes.category)
    if ($attributes.sensitive -eq $true) {
        Add-LdcFinding -Platform TerraformCloud -Scope $Scope -Resource $VariableId -Classification SecretMetadata `
            -Application $key -Environment '' -SecretReference $OwnerId -SecretVersion '' -Owner '' `
            -FirstObservedBucket '' -LastObservedBucket '' -Evidence "key=$key; category=$category; sensitive=true; owner=$OwnerId." `
            -NextStep 'Review the sensitive variable in its owner-controlled Terraform scope.'
        Add-LdcGap -Platform TerraformCloud -Scope $Scope -Resource $VariableId -Reason 'MaskedTerraformVariable' `
            -NextStep 'Review this sensitive Terraform variable in its owner-controlled scope; its value was not read by this collector.'
        return
    }
    if (-not $attributes.Contains('value') -or $attributes.value -isnot [string]) {
        Add-LdcGap -Platform TerraformCloud -Scope $Scope -Resource $VariableId -Reason 'UnreadableTerraformVariable'
        return
    }
    $value = [string]$attributes.value
    if (Test-LdcSecret $value) {
        Add-LdcFinding -Platform TerraformCloud -Scope $Scope -Resource $VariableId -Classification ExactStoredKeyMatch `
            -Application $OwnerId -Environment '' -SecretReference $OwnerId -SecretVersion '' -Owner '' `
            -FirstObservedBucket '' -LastObservedBucket '' -Evidence "key=$key; category=$category; exact ordinal local comparison; value withheld." `
            -NextStep 'Rotate the discovered credential through its owning Terraform scope.'
    }
}

function Invoke-LdcTerraformWorkspaceVariables {
    param([string]$Organization, [string]$WorkspaceId)
    $scope = "organizations/$Organization/workspaces/$WorkspaceId"
    Invoke-LdcTerraformPages -Scope $scope -CollectionPath "/api/v2/workspaces/$WorkspaceId/vars" -ExpectedType vars -AllowUnpaginated -OnItem {
        param($variable)
        Add-LdcTerraformVariableFinding $scope $WorkspaceId ([string]$variable.id) $variable
    }
}

function Invoke-LdcTerraformWorkspaceRuns {
    param([string]$Organization, [string]$WorkspaceId)
    $scope = "organizations/$Organization/workspaces/$WorkspaceId"
    Invoke-LdcTerraformPages -Scope $scope -CollectionPath "/api/v2/workspaces/$WorkspaceId/runs" -ExpectedType runs -OnItem {
        param($run)
        $runId = [string]$run.id
        if (-not (Test-LdcTerraformId $runId run) -or $run.attributes -isnot [Collections.IDictionary] -or
            -not $run.attributes.Contains('created-at')) {
            Add-LdcGap -Platform TerraformCloud -Scope $scope -Resource $WorkspaceId -Reason 'MalformedTerraformRun'; return
        }
        try { $created = [datetimeoffset]::Parse([string]$run.attributes['created-at']) } catch {
            Add-LdcGap -Platform TerraformCloud -Scope $scope -Resource $runId -Reason 'MalformedTerraformRunTimestamp'; return
        }
        if ($created -lt $script:Ldc.From -or $created -gt $script:Ldc.To) { return }
        $status = Protect-LdcText ([string]$run.attributes.status)
        Add-LdcFinding -Platform TerraformCloud -Scope $scope -Resource $runId -Classification WorkspaceActivity `
            -Application '' -Environment '' -SecretReference $WorkspaceId -SecretVersion '' -Owner '' `
            -FirstObservedBucket '' -LastObservedBucket '' `
            -Evidence "created-at=$($created.ToString('o')); status=$status; metadata-only run inventory." -NextStep 'Use workspace activity only as context; it does not establish token consumption.'
    }
}

function Get-LdcTerraformInlineRelationshipIds {
    param([string]$Scope, [string]$VarsetId, $VariableSet, [string]$Relationship, [string]$ExpectedType)
    $ids = [Collections.Generic.List[string]]::new()
    if ($VariableSet.relationships -isnot [Collections.IDictionary] -or
        $VariableSet.relationships[$Relationship] -isnot [Collections.IDictionary] -or
        -not $VariableSet.relationships[$Relationship].Contains('data')) {
        Add-LdcGap -Platform TerraformCloud -Scope $Scope -Resource $VarsetId -Reason "MissingVariableSet$Relationship`Relationship"
        return @($ids)
    }
    $data = $VariableSet.relationships[$Relationship].data
    if ($null -eq $data) { return @($ids) }
    if ($data -isnot [array]) { Add-LdcGap -Platform TerraformCloud -Scope $Scope -Resource $VarsetId -Reason "MalformedVariableSet$Relationship`Relationship"; return @($ids) }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($item in $data) {
        if ($item -isnot [Collections.IDictionary] -or [string]$item.type -cne $ExpectedType -or -not [string]$item.id -or -not $seen.Add([string]$item.id)) {
            Add-LdcGap -Platform TerraformCloud -Scope $Scope -Resource $VarsetId -Reason "MalformedVariableSet$Relationship`Relationship"; continue
        }
        $ids.Add([string]$item.id)
    }
    return @($ids)
}

function Invoke-LdcTerraformVariableSet {
    param([string]$Organization, $VariableSet)
    $varsetId = [string]$VariableSet.id
    $scope = "organizations/$Organization/varsets/$varsetId"
    if (-not (Test-LdcTerraformId $varsetId varset) -or $VariableSet.attributes -isnot [Collections.IDictionary]) {
        Add-LdcGap -Platform TerraformCloud -Scope "organizations/$Organization" -Resource $varsetId -Reason 'MalformedTerraformVariableSet'; return
    }
    # Register before the variable child request.
    [void]$script:Ldc.TerraformVariableSets.Add($varsetId)
    $attributes = $VariableSet.attributes
    $name = Protect-LdcText ([string]$attributes.name)
    if (-not $attributes.Contains('global') -or $attributes.global -isnot [bool]) { Add-LdcGap -Platform TerraformCloud -Scope $scope -Resource $varsetId -Reason 'MalformedVariableSetGlobalScope' }
    # HashiCorp documents workspace/project relationship endpoints as POST/DELETE,
    # not GET. Retain only relationships included in the listed varset response.
    $workspaceIds = Get-LdcTerraformInlineRelationshipIds $scope $varsetId $VariableSet workspaces workspaces
    $projectIds = Get-LdcTerraformInlineRelationshipIds $scope $varsetId $VariableSet projects projects
    if ($attributes.global -is [bool] -and $attributes.global) {
        Add-LdcFinding -Platform TerraformCloud -Scope $scope -Resource $varsetId -Classification VariableSetGlobalBinding `
            -Application $name -Environment '' -SecretReference $varsetId -SecretVersion '' -Owner '' -FirstObservedBucket '' -LastObservedBucket '' `
            -Evidence 'Variable set applies globally within its organization.' -NextStep 'Route any variable-set match to the organization-wide variable-set owner.'
    }
    foreach ($workspaceId in $workspaceIds) {
        if ((Test-LdcTerraformId $workspaceId workspace) -and $script:Ldc.TerraformWorkspaces.Contains($workspaceId)) {
            Add-LdcFinding -Platform TerraformCloud -Scope $scope -Resource $workspaceId -Classification VariableSetWorkspaceBinding `
                -Application $name -Environment '' -SecretReference $varsetId -SecretVersion '' -Owner '' -FirstObservedBucket '' -LastObservedBucket '' `
                -Evidence 'Variable set applies to this verified workspace.' -NextStep 'Route any variable-set match to this workspace and variable-set owner.'
        } else {
            Add-LdcGap -Platform TerraformCloud -Scope $scope -Resource $varsetId -Reason 'UnverifiedVariableSetWorkspace'
            if (Test-LdcTerraformId $workspaceId workspace) {
                Add-LdcFinding -Platform TerraformCloud -Scope $scope -Resource $workspaceId -Classification UnresolvedCandidate `
                    -Application $name -SecretReference $varsetId -Evidence 'Variable-set workspace relationship; workspace was not independently inventoried.' `
                    -NextStep 'Verify workspace access and applicability; this relationship does not authorize expanding the scan.'
            }
        }
    }
    foreach ($projectId in $projectIds) {
        if (-not (Test-LdcTerraformId $projectId project)) { Add-LdcGap -Platform TerraformCloud -Scope $scope -Resource $varsetId -Reason 'MalformedVariableSetProject'; continue }
        Add-LdcFinding -Platform TerraformCloud -Scope $scope -Resource $projectId -Classification VariableSetProjectBinding `
            -Application $name -Environment '' -SecretReference $varsetId -SecretVersion '' -Owner '' -FirstObservedBucket '' -LastObservedBucket '' `
            -Evidence 'Variable set applies to this project.' -NextStep 'Route any variable-set match to this project and variable-set owner.'
    }
    try {
        Invoke-LdcTerraformPages -Scope $scope -CollectionPath "/api/v2/varsets/$varsetId/relationships/vars" -ExpectedType vars -OnItem {
            param($variable)
            Add-LdcTerraformVariableFinding $scope $varsetId ([string]$variable.id) $variable
        }
    } catch { Add-LdcGap -Platform TerraformCloud -Scope $scope -Resource $varsetId -Reason (Get-LdcFailureCode $_) }
}

function Invoke-LdcTerraformAccountIdentity {
    $scope = 'account/details'
    Invoke-LdcScope -Platform TerraformIdentity -Scope $scope -Action {
        $data = (Invoke-LdcRequest -Platform TerraformIdentity -Uri (Get-LdcTerraformUri '/api/v2/account/details')).Data
        if ($data -isnot [Collections.IDictionary] -or $data.data -isnot [Collections.IDictionary] -or
            [string]$data.data.type -cne 'users' -or -not (Test-LdcTerraformId ([string]$data.data.id) account)) {
            throw 'LDC:invalid-terraform-account-details'
        }
        $resource = $null
        if ($data.data.relationships -is [Collections.IDictionary] -and
            $data.data.relationships['authenticated-resource'] -is [Collections.IDictionary]) {
            $candidate = $data.data.relationships['authenticated-resource'].data
            if ($candidate -is [Collections.IDictionary] -and [string]$candidate.type -in @('users', 'teams', 'organizations') -and
                (Test-LdcTerraformId ([string]$candidate.id) account)) { $resource = $candidate }
            elseif ($null -ne $candidate) { Add-LdcGap -Platform TerraformIdentity -Scope $scope -Resource ([string]$data.data.id) -Reason 'MalformedAuthenticatedResource' }
        }
        if ($null -eq $resource -and $data.data.attributes -is [Collections.IDictionary] -and $data.data.attributes['is-service-account'] -eq $false) {
            $resource = @{ type = 'users'; id = [string]$data.data.id }
        }
        if ($null -eq $resource) { Add-LdcGap -Platform TerraformIdentity -Scope $scope -Resource ([string]$data.data.id) -Reason 'AuthenticatedResourceUnresolved'; return }
        $authTokenId = ''
        $authToken = if ($data.data.links -is [Collections.IDictionary]) { [string]$data.data.links['auth-token'] } else { '' }
        $configuredHost = [string]$script:Ldc.Config.terraformCloud.hostname
        if ($authToken) {
            try { $authTokenUri = [uri]::new([uri](Get-LdcTerraformUri '/api/v2/account/details'), $authToken) } catch { $authTokenUri = $null }
            if ($authTokenUri -and $authTokenUri.IsAbsoluteUri -and $authTokenUri.Scheme -ceq 'https' -and
                $authTokenUri.Host -ceq $configuredHost -and $authTokenUri.Port -eq 443 -and -not $authTokenUri.UserInfo -and
                -not $authTokenUri.Fragment -and -not $authTokenUri.Query -and
                $authTokenUri.AbsolutePath -match '^/api/v2/authentication-tokens/(at-[A-Za-z0-9]+)$') { $authTokenId = $Matches[1] }
            else { Add-LdcGap -Platform TerraformIdentity -Scope $scope -Resource ([string]$resource.id) -Reason 'InvalidAuthTokenLink' }
        } else { Add-LdcGap -Platform TerraformIdentity -Scope $scope -Resource ([string]$resource.id) -Reason 'MissingAuthTokenLink' }
        $evidence = "authenticated-resource=$($resource.type)/$($resource.id); actor identity only."
        if ($authTokenId) { $evidence = "$evidence auth-token-id=$authTokenId." }
        Add-LdcFinding -Platform TerraformIdentity -Scope $scope -Resource (Protect-LdcText ("$($resource.type)/$($resource.id)")) `
            -Classification AuthenticatedIdentity -Application '' -Environment '' -SecretReference '' -SecretVersion '' -Owner '' `
            -FirstObservedBucket '' -LastObservedBucket '' -Evidence (Protect-LdcText $evidence) `
            -NextStep 'Identity is provider-reported actor context only; no token metadata endpoint was queried.'
    }
}

function Invoke-LdcTerraformWorkspace {
    param([string]$Organization, [string]$OrganizationScope, $Workspace)
    $workspaceId = [string]$Workspace.id
    if (-not (Test-LdcTerraformId $workspaceId workspace)) { throw 'LDC:invalid-terraform-workspace-id' }
    # Register before either child collection is requested.
    [void]$script:Ldc.TerraformWorkspaces.Add($workspaceId)
    if ($Workspace.attributes -isnot [Collections.IDictionary]) { Add-LdcGap TerraformCloud $OrganizationScope $workspaceId 'MalformedTerraformWorkspace'; return }
    $a = $Workspace.attributes
    $vcs = if ($a['vcs-repo'] -is [Collections.IDictionary]) { [string]$a['vcs-repo'].identifier } else { [string]$a['vcs-repo-identifier'] }
    $project = if ($Workspace.relationships -is [Collections.IDictionary] -and $Workspace.relationships.project -is [Collections.IDictionary] -and $Workspace.relationships.project.data -is [Collections.IDictionary]) { [string]$Workspace.relationships.project.data.id } else { '' }
    Add-LdcFinding -Platform TerraformCloud -Scope $OrganizationScope -Resource $workspaceId -Classification WorkspaceInventory `
        -Application (Protect-LdcText ([string]$a.name)) -Environment '' -SecretReference $workspaceId -SecretVersion '' -Owner '' `
        -FirstObservedBucket '' -LastObservedBucket '' -Evidence (Protect-LdcText ("vcs=$vcs; working-directory=$($a['working-directory']); execution-mode=$($a['execution-mode']); project=$project.")) `
        -NextStep 'Use workspace metadata to route any credential match to its workspace owner.'
    try { Invoke-LdcTerraformWorkspaceVariables $Organization $workspaceId } catch { Add-LdcGap TerraformCloud $OrganizationScope $workspaceId (Get-LdcFailureCode $_) }
    try { Invoke-LdcTerraformWorkspaceRuns $Organization $workspaceId } catch { Add-LdcGap TerraformCloud $OrganizationScope $workspaceId (Get-LdcFailureCode $_) }
}

function Invoke-LdcTerraformCloud {
    Invoke-LdcTerraformAccountIdentity
    $config = $script:Ldc.Config.terraformCloud
    foreach ($organization in @($config.organizations | Where-Object { $_ })) {
        $organization = [string]$organization
        $scope = "organizations/$organization"
        Invoke-LdcScope -Platform TerraformCloud -Scope "$scope/workspaces" -Action {
            Invoke-LdcTerraformPages -Scope $scope -CollectionPath ("/api/v2/organizations/$([uri]::EscapeDataString($organization))/workspaces") -ExpectedType workspaces -OnItem {
                param($workspace); Invoke-LdcTerraformWorkspace $organization $scope $workspace
            }
        }
        Invoke-LdcScope -Platform TerraformCloud -Scope "$scope/varsets" -Action {
            Invoke-LdcTerraformPages -Scope $scope -CollectionPath ("/api/v2/organizations/$([uri]::EscapeDataString($organization))/varsets") -ExpectedType varsets -OnItem {
                param($varset)
                $varsetId = [string]$varset.id
                if (-not (Test-LdcTerraformId $varsetId varset)) { throw 'LDC:invalid-terraform-varset-id' }
                [void]$script:Ldc.TerraformVariableSets.Add($varsetId)
                Invoke-LdcTerraformVariableSet $organization $varset
            }
        }
    }
}
