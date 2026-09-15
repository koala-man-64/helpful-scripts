$script:LdcCloudMaxPages = 10000

function Get-LdcCloudOwner {
    param([System.Collections.IDictionary]$Labels)
    if ($null -eq $Labels) { return $null }
    $values = [Collections.Generic.List[string]]::new()
    foreach ($key in 'owner', 'team', 'application') {
        if ($Labels.ContainsKey($key) -and $Labels[$key]) { $values.Add("$key=$($Labels[$key])") }
    }
    if ($values.Count) { return [string]::Join(';', $values) }
    return $null
}

function Get-LdcCloudLabels {
    param([System.Collections.IDictionary]$Item, [string]$Name = 'tags')
    if ($Item.ContainsKey($Name) -and $Item[$Name] -is [System.Collections.IDictionary]) { return $Item[$Name] }
    return @{}
}

function Add-LdcCloudFinding {
    param([string]$Platform,[string]$Scope,[string]$Resource,[string]$Classification,[System.Collections.IDictionary]$Labels,[string]$SecretReference,[string]$SecretVersion,[string]$Evidence)
    $parameters = @{ Platform=$Platform; Scope=$Scope; Resource=$Resource; Classification=$Classification; Evidence=$Evidence }
    $owner = Get-LdcCloudOwner $Labels
    if ($owner) { $parameters.Owner = $owner }
    if ($SecretReference) { $parameters.SecretReference = $SecretReference }
    if ($SecretVersion) { $parameters.SecretVersion = $SecretVersion }
    Add-LdcFinding @parameters
}

function Test-LdcCloudUri {
    param([string]$Uri,[string[]]$AllowedHosts,[string]$PathPrefix)
    $parsed = $null
    if (-not [uri]::TryCreate($Uri, [uriKind]::Absolute, [ref]$parsed)) { return $false }
    return $parsed.Scheme -eq 'https' -and $parsed.Port -eq 443 -and -not $parsed.UserInfo -and -not $parsed.Fragment -and
        $AllowedHosts -contains $parsed.Host -and $parsed.AbsolutePath -ceq $PathPrefix
}

function Get-LdcCloudCollection {
    param([System.Collections.IDictionary]$Data,[string[]]$Keys,[switch]$AllowEmptyObject)
    foreach ($key in $Keys) {
        if (-not $Data.ContainsKey($key)) { continue }
        $value = $Data[$key]
        if ($null -eq $value -or $value -is [string] -or $value -isnot [System.Collections.IEnumerable]) { return $null }
        return @{ Items = [object[]]$value }
    }
    if ($AllowEmptyObject -and $Data.Count -eq 0) { return @{ Items = [object[]]@() } }
    return $null
}

function Invoke-LdcCloudPages {
    param([string]$Platform,[string]$Scope,[string]$InitialUri,[string[]]$AllowedHosts,[string]$PathPrefix,[string]$Subscription,[string[]]$CollectionKeys,[switch]$AllowEmptyObject,[scriptblock]$OnItem)
    $uri = $InitialUri
    $seenUris = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $seenIds = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    for ($page = 1; $page -le $script:LdcCloudMaxPages; $page++) {
        if (-not (Test-LdcCloudUri $uri $AllowedHosts $PathPrefix)) { Add-LdcGap $Platform $Scope $InitialUri 'InvalidContinuation' 'Review the provider continuation target.'; return }
        if (-not $seenUris.Add($uri)) { Add-LdcGap $Platform $Scope $InitialUri 'DuplicateContinuation' 'Review provider pagination.'; return }
        try { $request=@{Platform=$Platform;Uri=$uri}; if($Subscription){$request.Subscription=$Subscription}; $response=Invoke-LdcRequest @request }
        catch { Add-LdcGap $Platform $Scope $InitialUri (Get-LdcFailureCode $_) 'Resolve the reported inventory failure and rerun this scope.'; return }
        if ($null -eq $response -or $response.Data -isnot [System.Collections.IDictionary]) { Add-LdcGap $Platform $Scope $InitialUri 'MalformedInventoryResponse' 'Review the provider response schema.'; return }
        $collection = Get-LdcCloudCollection $response.Data $CollectionKeys -AllowEmptyObject:$AllowEmptyObject
        if ($null -eq $collection) { Add-LdcGap $Platform $Scope $InitialUri 'MalformedInventoryResponse' 'Review the provider response schema.'; return }
        foreach ($item in $collection.Items) {
            if ($item -isnot [System.Collections.IDictionary]) { Add-LdcGap $Platform $Scope $InitialUri 'MalformedInventoryItem' 'Review the provider response schema.'; continue }
            $id = if ($item.ContainsKey('id')) {[string]$item.id} elseif ($item.ContainsKey('name')) {[string]$item.name} else {''}
            if (-not $id -or -not $seenIds.Add($id)) { Add-LdcGap $Platform $Scope $InitialUri 'DuplicateOrMissingResourceId' 'Review inventory consistency.'; continue }
            & $OnItem $item
        }
        $next = if ($response.Data.ContainsKey('nextLink')) {[string]$response.Data.nextLink} elseif ($response.Data.ContainsKey('nextPageToken') -and $response.Data.nextPageToken) { $separator=if($InitialUri.Contains('?')){'&'}else{'?'}; "$InitialUri$separator`pageToken=$([uri]::EscapeDataString([string]$response.Data.nextPageToken))" } else {$null}
        if (-not $next) { return }; $uri = $next
    }
    Add-LdcGap $Platform $Scope $InitialUri 'PaginationLimitExceeded' 'Narrow scope or investigate pagination.'
}

function Get-LdcAzureVaultHost {
    param([string]$SubscriptionId,[System.Collections.IDictionary]$Vault)
    $vaultId = [string]$Vault.id
    $idPattern = '^/subscriptions/' + [regex]::Escape($SubscriptionId) + '/resourceGroups/[^/]+/providers/Microsoft\.KeyVault/vaults/([A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9])$'
    if ($vaultId -notmatch $idPattern) { return $null }
    $name = $Matches[1]; $parsed = $null
    if (-not [uri]::TryCreate([string]$Vault.properties.vaultUri,[uriKind]::Absolute,[ref]$parsed)) { return $null }
    if ($parsed.Scheme -ne 'https' -or $parsed.Port -ne 443 -or $parsed.UserInfo -or $parsed.Query -or $parsed.Fragment -or $parsed.AbsolutePath -ne '/' -or $parsed.Host -ine "$name.vault.azure.net") { return $null }
    return $parsed.Host
}

function Invoke-LdcAzureVault {
    param([string]$SubscriptionId,[System.Collections.IDictionary]$Vault)
    $scope="subscriptions/$SubscriptionId"; $vaultId=[string]$Vault.id
    $idPattern='^/subscriptions/'+[regex]::Escape($SubscriptionId)+'/resourceGroups/[^/]+/providers/Microsoft\.KeyVault/vaults/[A-Za-z][A-Za-z0-9-]{1,22}[A-Za-z0-9]$'
    if ($vaultId -notmatch $idPattern) { Add-LdcGap Azure $scope $vaultId 'InvalidVaultIdentity' 'Verify the inventory resource identity.'; return }
    if (-not $Vault.properties.vaultUri) {
        try {
            $detail=(Invoke-LdcRequest -Platform Azure -Subscription $SubscriptionId -Uri "https://management.azure.com${vaultId}?api-version=2023-07-01").Data
            if ($detail.id -ine $vaultId) { throw 'LDC:vault-identity-mismatch' }
            $Vault=$detail
        } catch { Add-LdcGap Azure $scope $vaultId (Get-LdcFailureCode $_) 'Resolve vault metadata access and rerun.'; return }
    }
    $vaultHost=Get-LdcAzureVaultHost $SubscriptionId $Vault
    if (-not $vaultHost) { Add-LdcGap Azure $scope $vaultId 'InvalidVaultIdentity' 'Verify the resource ID and vault URI before scanning.'; return }
    [void]$script:Ldc.AllowedVaultHosts.Add($vaultHost)
    $labels=Get-LdcCloudLabels $Vault; $base="https://$vaultHost"
    Add-LdcCloudFinding Azure $scope $vaultId ResourceInventory $labels $null $null "type=Microsoft.KeyVault/vaults; location=$($Vault.location)."
    Invoke-LdcCloudPages KeyVault $scope "$base/secrets?api-version=7.4" @($vaultHost) '/secrets' $SubscriptionId @('value') -AllowEmptyObject:$false -OnItem {
        param($secret)
        $reference=([string]$secret.id).TrimEnd('/').ToLowerInvariant()
        if ($reference -notmatch ('^'+[regex]::Escape($base)+'/secrets/[a-z0-9-]+$')) { Add-LdcGap KeyVault $scope $vaultId 'MalformedSecretMetadata' 'Review Key Vault secret metadata.'; return }
        $name=$reference.Split('/')[-1]; Add-LdcCloudFinding KeyVault $scope $reference ResourceInventory $labels $reference $null 'type=Microsoft.KeyVault/vaults/secrets; location=provider-supplied.'
        $prefix='/secrets/'+[uri]::EscapeDataString($name)+'/versions'
        Invoke-LdcCloudPages KeyVault $scope "$base$prefix`?api-version=7.4" @($vaultHost) $prefix $SubscriptionId @('value') -AllowEmptyObject:$false -OnItem {
            param($version)
            $versionUri=([string]$version.id).TrimEnd('/'); $expected="$reference/"
            if (-not $versionUri.StartsWith($expected,[StringComparison]::OrdinalIgnoreCase) -or $versionUri.Split('/').Count -ne 6) { Add-LdcGap KeyVault $scope $reference 'MalformedVersionMetadata' 'Review Key Vault version metadata.'; return }
            $versionId=$versionUri.Split('/')[-1]
            Add-LdcCloudFinding KeyVault $scope $versionUri SecretVersionInventory $labels $reference $versionId 'type=Microsoft.KeyVault/vaults/secrets/versions; location=provider-supplied.'
            if ($version.attributes.enabled -ne $true) { Add-LdcGap KeyVault $scope $versionUri 'SecretVersionDisabled' 'Record the disabled version; no enablement is recommended by this read-only scanner.'; return }
            try {
                $data=Invoke-LdcRequest -Platform KeyVault -Uri "$versionUri`?api-version=7.4" -Subscription $SubscriptionId
                if ($data.Data.value -isnot [string] -or $data.Data.id -ine $versionUri) { throw 'LDC:secret-response-mismatch' }
                if(Test-LdcSecret ([string]$data.Data.value)){Add-LdcCloudFinding KeyVault $scope $versionUri ExactStoredKeyMatch $labels $reference $versionId 'Exact ordinal local comparison; value withheld.'}
            } catch { Add-LdcGap KeyVault $scope $versionUri (Get-LdcFailureCode $_) 'Resolve this version access or response failure and rerun.' }
        }
    }
}

function ConvertFrom-LdcGcpSecretPayload {
    param([string]$Payload)
    try { return [Text.UTF8Encoding]::new($false,$true).GetString([Convert]::FromBase64String($Payload)) } catch { return $null }
}

function Invoke-LdcGcpProject {
    param([string]$ProjectId)
    $scope="projects/$ProjectId"
    try {
        $project=(Invoke-LdcRequest -Platform GCP -Uri "https://cloudresourcemanager.googleapis.com/v3/projects/$([uri]::EscapeDataString($ProjectId))").Data
        if ($project.name -cnotmatch '^projects/([0-9]{1,20})$') { throw 'LDC:invalid-project-identity' }
        $number=$Matches[1]
        if ($project.projectId -cne $ProjectId -and $number -cne $ProjectId) { throw 'LDC:invalid-project-identity' }
        $script:Ldc.GcpProjectAliases[$ProjectId]=$number
    }
    catch { Add-LdcGap GCP $scope $scope 'ProjectIdentityUnresolved' 'Grant project metadata read permission and retry.' }
    Invoke-LdcCloudPages GCP $scope "https://cloudasset.googleapis.com/v1/$scope`:searchAllResources" @('cloudasset.googleapis.com') "/v1/$scope`:searchAllResources" $null @('results') -AllowEmptyObject:$true -OnItem {
        param($asset)
        $name=[string]$asset.name; if(-not $name){Add-LdcGap GCP $scope $scope 'MalformedAssetMetadata' 'Review Cloud Asset response.';return}
        $type=[string]$asset.assetType; $location=if($asset.ContainsKey('location')){[string]$asset.location}else{'global-or-unspecified'}; Add-LdcCloudFinding GCP $scope $name ResourceInventory (Get-LdcCloudLabels $asset 'labels') $null $null "type=$type; location=$location."
        if($type -eq 'secretmanager.googleapis.com/Secret' -and $name -match '^//secretmanager\.googleapis\.com/projects/[^/]+/locations/[^/]+/secrets/[^/]+$'){Add-LdcGap GCP $scope $name 'UnsupportedRegionalSecretManagerResource' 'Use a collector that explicitly supports regional Secret Manager resources.'}
    }
    Invoke-LdcCloudPages GCP $scope "https://secretmanager.googleapis.com/v1/$scope/secrets" @('secretmanager.googleapis.com') "/v1/$scope/secrets" $null @('secrets') -AllowEmptyObject:$true -OnItem {
        param($secret)
        $name=[string]$secret.name
        if($name -notmatch '^projects/([^/]+)/secrets/[^/]+$'){Add-LdcGap GCP $scope $scope 'MalformedSecretMetadata' 'Review Secret Manager response.';return}
        $project=$Matches[1];$alias=[string]$script:Ldc.GcpProjectAliases[$ProjectId]
        if($project -cne $ProjectId -and $project -cne $alias){Add-LdcGap GCP $scope $name 'CrossScopeSecretResource' 'Review project identity and provider inventory scope.';return}
        $labels=Get-LdcCloudLabels $secret 'labels'; Add-LdcCloudFinding GCP $scope $name ResourceInventory $labels $name $null 'type=secretmanager.googleapis.com/Secret; location=global.'
        $prefix="/v1/$name/versions"
        Invoke-LdcCloudPages GCP $scope "https://secretmanager.googleapis.com$prefix" @('secretmanager.googleapis.com') $prefix $null @('versions') -AllowEmptyObject:$true -OnItem {
            param($version)
            $versionName=[string]$version.name
            if($versionName -cnotmatch ('^'+[regex]::Escape($name)+'/versions/[0-9]+$')){Add-LdcGap GCP $scope $name 'CrossScopeSecretVersion' 'Review Secret Manager version identity.';return}
            $versionId=$versionName.Split('/')[-1]; Add-LdcCloudFinding GCP $scope $versionName SecretVersionInventory $labels $name $versionId 'type=secretmanager.googleapis.com/SecretVersion; location=global.'
            if([string]$version.state -cne 'ENABLED'){Add-LdcGap GCP $scope $versionName 'SecretVersionDisabled' 'Record the disabled version; no enablement is recommended by this read-only scanner.';return}
            try {
                $data=Invoke-LdcRequest -Platform GCP -Uri "https://secretmanager.googleapis.com/v1/$versionName`:access"
                if ($data.Data.name -cne $versionName -or $data.Data.payload.data -isnot [string]) { throw 'LDC:secret-response-mismatch' }
                $value=ConvertFrom-LdcGcpSecretPayload $data.Data.payload.data
                if($null -eq $value){Add-LdcGap GCP $scope $versionName 'SecretPayloadNotUtf8' 'Record the undecodable version for owner review.'}
                elseif(Test-LdcSecret $value){Add-LdcCloudFinding GCP $scope $versionName ExactStoredKeyMatch $labels $name $versionId 'Exact ordinal local comparison; value withheld.'}
            } catch{Add-LdcGap GCP $scope $versionName (Get-LdcFailureCode $_) 'Resolve this version access or response failure and rerun.'}
        }
    }
}

function Invoke-LdcCloud {
    foreach($subscriptionId in @($script:Ldc.Config.azure.subscriptionIds|Where-Object{$_})){
        $scope="subscriptions/$subscriptionId"; Invoke-LdcScope -Platform Azure -Scope $scope -Action {
            $path="/subscriptions/$([uri]::EscapeDataString($subscriptionId))/resources"
            Invoke-LdcCloudPages Azure $scope "https://management.azure.com$path`?api-version=2021-04-01" @('management.azure.com') $path $subscriptionId @('value') -AllowEmptyObject:$false -OnItem {
                param($resource); $type=if($resource.ContainsKey('type')){[string]$resource.type}else{''}; if($type -ceq 'Microsoft.KeyVault/vaults'){Invoke-LdcAzureVault $subscriptionId $resource;return}; $location=if($resource.ContainsKey('location')){[string]$resource.location}else{'provider-supplied'};Add-LdcCloudFinding Azure $scope ([string]$resource.id) ResourceInventory (Get-LdcCloudLabels $resource) $null $null "type=$type; location=$location."
            }
        }
    }
    foreach($projectId in @($script:Ldc.Config.gcp.projectIds|Where-Object{$_})){Invoke-LdcScope -Platform GCP -Scope "projects/$projectId" -Action {Invoke-LdcGcpProject $projectId}}
}
