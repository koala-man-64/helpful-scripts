# Bounded, metadata-only repository collector. Core.ps1 owns transport and report sanitisation.
function Get-LdcValue {
    param($Object, [string]$Name, $Default = $null) if ($null -eq $Object) {
        return $Default
    }; if ($Object -is [Collections.IDictionary] -and $Object.Contains($Name)) {
        return $Object[$Name]
    }; $p = $Object.PSObject.Properties[$Name]; if ($p) {
        return $p.Value
    }; $Default
}
function Get-LdcLimit {
    param([string]$Name, [int]$Default)
    # The public configuration schema is fixed. These conservative ceilings are collector policy.
    switch ($Name) {
        'maxContentCharacters' {
            return 1048576
        }; 'maxReferenceDepth' {
            return 8
        }; 'maxReferenceFiles' {
            return 100
        }; 'maxSourceFiles' {
            return 1000
        }; default {
            return $Default
        }
    }
}
function Add-LdcCollectorGap {
    param($Platform, $Scope, $Resource, $Reason) Add-LdcGap -Platform $Platform -Scope $Scope -Resource $Resource -Reason $Reason -NextStep 'Inspect the bounded provider response and rerun the explicit scope.'
}
function ConvertFrom-LdcContent {
    param($Response, [ValidateSet('GitHub', 'AzureDevOps')]$Platform)
    $d = Get-LdcValue $Response Data $Response; if ($d -is [string]) {
        try {
            $d = ConvertFrom-Json $d -AsHashtable -Depth 32 -ErrorAction Stop
        }
        catch {
            return $d
        }
    }; $content = Get-LdcValue $d content $null; if ($null -eq $content) {
        return $null
    }; $encoding = [string](Get-LdcValue $d encoding ''); if ($encoding -eq 'base64' -or ($Platform -eq 'GitHub' -and !$encoding)) {
        try {
            return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(([string]$content -replace '\s', '')))
        }
        catch {
            return $null
        }
    }; return [string]$content
}
function Get-LdcPaged {
    param([ValidateSet('GitHub', 'AzureDevOps')]$Platform, [string]$Uri)
    $out = [Collections.Generic.List[object]]::new(); $uris = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal); $ids = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal); $next = if ($Platform -eq 'GitHub') {
        "$Uri$(if($Uri.Contains('?')){'&'}else{'?'})per_page=100&page=1"
    }
    else {
        "$Uri$(if($Uri.Contains('?')){'&'}else{'?'})`$top=100"
    }
    for ($page = 1; $page -le $script:Ldc.MaxPages; $page++) {
        if (!$uris.Add($next)) {
            Add-LdcCollectorGap $Platform $Uri $Uri 'PAGINATION_DUPLICATE_OR_CYCLE'; break
        }; try {
            $r = Invoke-LdcRequest -Platform $Platform -Uri $next
        }
        catch {
            Add-LdcCollectorGap $Platform $Uri $next (Get-LdcFailureCode $_); break
        }
        $d = $r.Data; $items = $null
        if ($Platform -eq 'GitHub' -and $d -is [array]) { $items = $d }
        elseif ($d -is [Collections.IDictionary]) {
            $fields = if ($Platform -eq 'GitHub') { @('items','repositories','workflows','secrets','environments') } else { @('value') }
            foreach ($field in $fields) { if ($d.Contains($field)) { $items = $d[$field]; break } }
        }
        if ($items -isnot [array]) { Add-LdcCollectorGap $Platform $Uri $Uri 'INVALID_COLLECTION_RESPONSE'; break }
        foreach ($i in $items) {
            if ($null -eq $i) {
                continue
            }; $id = [string](Get-LdcValue $i id (Get-LdcValue $i node_id (Get-LdcValue $i path (Get-LdcValue $i name '')))); if (!$id) {
                Add-LdcCollectorGap $Platform $Uri $Uri 'INVALID_COLLECTION_ITEM'; continue
            }; if (!$ids.Add($id)) {
                Add-LdcCollectorGap $Platform $Uri $Uri 'PAGINATION_DUPLICATE_OR_CYCLE'; return $out.ToArray()
            }; $out.Add($i)
        }; $h = Get-LdcValue $r Headers @{}; if ($Platform -eq 'AzureDevOps') {
            $token = Get-LdcValue $h 'x-ms-continuationtoken' (Get-LdcValue $h 'X-MS-ContinuationToken' ''); if (!$token) {
                break
            }; $next = "$Uri$(if($Uri.Contains('?')){'&'}else{'?'})`$top=100&continuationToken=$([uri]::EscapeDataString($token))"
        }
        else {
            $link = [string](Get-LdcValue $h Link (Get-LdcValue $h link '')); $m = [regex]::Match($link, '<([^>]+)>;\s*rel="?next"?', 'IgnoreCase'); if ($m.Success) {
                $next = $m.Groups[1].Value
                $candidate = $null
                if (![uri]::TryCreate($next, [UriKind]::Absolute, [ref]$candidate) -or
                    $candidate.Scheme -ne 'https' -or $candidate.Authority -ne ([uri]$Uri).Authority -or
                    $candidate.AbsolutePath -cne ([uri]$Uri).AbsolutePath -or $candidate.UserInfo -or $candidate.Fragment) {
                    Add-LdcCollectorGap $Platform $Uri $Uri 'INVALID_COLLECTION_CONTINUATION'; break
                }
            }
            elseif (@($items).Count -ge 100) {
                $next = "$Uri$(if($Uri.Contains('?')){'&'}else{'?'})per_page=100&page=$($page+1)"
            }
            else {
                break
            }
        }; if ($page -eq $script:Ldc.MaxPages) {
            Add-LdcCollectorGap $Platform $Uri $Uri 'PAGINATION_LIMIT_REACHED'
        }
    }; return $out.ToArray()
}
function Add-LdcSecretReferences {
    param($Platform, $Scope, $Resource, $Text, $Application, $Environment)
    foreach ($m in [regex]::Matches($Text, '(?i)(https://[^\s"'']+\.vault\.azure\.net/secrets/[A-Za-z0-9-]+(?:/[A-Za-z0-9-]+)?|/subscriptions/[0-9a-f-]+/resourceGroups/[^\s"'']+/providers/Microsoft\.KeyVault/vaults/[^\s"'']+/secrets/[A-Za-z0-9-]+(?:/versions/[A-Za-z0-9-]+)?)')) {
        $full = $m.Groups[1].Value.TrimEnd('/', ' ', ')', ']', '"', ''''); $vm = [regex]::Match($full, '(?i)(?:/versions/|/secrets/[^/]+/)([A-Za-z0-9-]+)$'); $ver = if ($vm.Success) {
            $vm.Groups[1].Value
        }
        else {
            ''
        }; $base = if ($ver) {
            $full.Substring(0, $full.Length - $ver.Length).TrimEnd('/')
        }
        else {
            $full
        }
        if ($full -match '(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.KeyVault/vaults/([A-Za-z0-9-]+)/secrets/([A-Za-z0-9-]+)(?:/versions/([A-Za-z0-9-]+))?$') {
            $base = "https://$($Matches[1]).vault.azure.net/secrets/$($Matches[2])"; $ver = $Matches[3]
        }
        Add-LdcFinding -Platform $Platform -Scope $Scope -Resource $Resource -Classification UnresolvedCandidate -Application $Application -Environment $Environment -SecretReference $base.ToLowerInvariant() -SecretVersion $ver -Evidence 'Static Key Vault secret reference; value not read.' -NextStep 'Compare through approved inventory; source presence is not runtime consumption.'
    }
    foreach ($m in [regex]::Matches($Text, '(?i)(projects/[A-Za-z0-9-]+/secrets/[A-Za-z0-9_-]+)(?:/versions/([A-Za-z0-9_-]+))?')) {
        Add-LdcFinding -Platform $Platform -Scope $Scope -Resource $Resource -Classification UnresolvedCandidate -Application $Application -Environment $Environment -SecretReference $m.Groups[1].Value -SecretVersion $m.Groups[2].Value -Evidence 'Static GCP Secret Manager reference; value not read.' -NextStep 'Compare through approved inventory; source presence is not runtime consumption.'
    }
}
function Get-LdcGitHubText {
    param($Repository, $Path, $Ref) try {
        $p = ($Path -split '/' | ForEach-Object { [uri]::EscapeDataString($_) }) -join '/'; ConvertFrom-LdcContent (Invoke-LdcRequest -Platform GitHub -Uri "https://api.github.com/repos/$Repository/contents/$p`?ref=$([uri]::EscapeDataString($Ref))" -Raw) GitHub
    }
    catch {
        Add-LdcCollectorGap GitHub $Repository "$Repository/$Path@$Ref" 'CONTENT_UNAVAILABLE'; $null
    }
}
function Add-LdcTextReferences {
    param($Platform, $Scope, $Resource, $Text, $Application, $Environment, $Repository, [string[]]$Allowed, [int]$Depth = 0)
    if ($null -eq $Text) {
        return
    }; if ($Text.Length -gt (Get-LdcLimit maxContentCharacters 1048576)) {
        Add-LdcCollectorGap $Platform $Scope $Resource 'CONTENT_LIMIT_REACHED'; return
    }; if ($Text -match '\$\{\{|\$\(') {
        Add-LdcCollectorGap $Platform $Scope $Resource 'DYNAMIC_REFERENCE_UNRESOLVED'
    }
    $tokenCharacters = if ($script:Ldc.CredentialKind -eq 'TerraformCloud') { 'A-Za-z0-9_.-' } else { 'A-Za-z0-9_-' }
    $known = [string]$script:Ldc.KnownSecret; if ($known -and [regex]::IsMatch($Text, "(?<![$tokenCharacters])" + [regex]::Escape($known) + "(?![$tokenCharacters])")) {
        [void](Test-LdcSecret $known); Add-LdcFinding -Platform $Platform -Scope $Scope -Resource $Resource -Classification ExactSourceKeyMatch -Application $Application -Environment $Environment -Evidence 'Exact key matched locally; value withheld.' -NextStep 'Rotate and remove the exposed credential.'
    }
    $candidatePattern = if ($script:Ldc.CredentialKind -eq 'TerraformCloud') {
        '(?i)(TF_TOKEN_|TFE_TOKEN|TFC_TOKEN|TERRAFORM_CLOUD_TOKEN|TF_CLI_CONFIG_FILE|cli_config_credentials_token|app\.(?:eu\.)?terraform\.io|hashicorp/setup-terraform|credentials\s*["{]|backend\s*"remote"|cloud\s*\{)'
    } else { '(?i)(sdk[-_ ]?key|client[-_ ]?side[-_ ]?id|launchdarkly)' }
    if ($Text -match $candidatePattern) {
        $label = if ($script:Ldc.CredentialKind -eq 'TerraformCloud') { 'Terraform Cloud' } else { 'LaunchDarkly' }
        Add-LdcFinding -Platform $Platform -Scope $Scope -Resource $Resource -Classification UnresolvedCandidate -Application $Application -Environment $Environment -Evidence "$label configuration candidate found; source text withheld." -NextStep 'Validate candidate through approved secret inventory.'
    }
    if ($script:Ldc.CredentialKind -eq 'TerraformCloud') {
        foreach ($reference in [regex]::Matches($Text, '\b(?:TF_TOKEN_[A-Za-z0-9_]+|TFE_TOKEN|TFC_TOKEN|TERRAFORM_CLOUD_TOKEN|TF_CLI_CONFIG_FILE)\b')) {
            Add-LdcFinding -Platform $Platform -Scope $Scope -Resource $Resource -Classification UnresolvedCandidate -Application $Application -Environment $Environment -SecretReference $reference.Value -Evidence 'Terraform CLI or automation credential variable name; value and runtime use unverified.' -NextStep 'Resolve this variable in the deployed runner or application environment.'
        }
    }
    if ($Platform -eq 'GitHub') {
        foreach ($reference in [regex]::Matches($Text, '(?im)\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)\b')) {
            Add-LdcFinding -Platform GitHub -Scope $Scope -Resource $Resource -Classification UnresolvedCandidate -Application $Application -Environment $Environment -SecretReference $reference.Groups[1].Value -Evidence 'Static GitHub Actions secret-name reference; value unavailable.' -NextStep 'Compare the name with repository, environment, or organization secret metadata.'
        }
    }; Add-LdcSecretReferences $Platform $Scope $Resource $Text $Application $Environment; if ($Platform -ne 'GitHub') {
        return
    }; if (!$script:Ldc.RepositoryReferences) {
        $script:Ldc.RepositoryReferences = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    }
    foreach ($m in [regex]::Matches($Text, '(?im)^\s*uses:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/[A-Za-z0-9_.-]+)@([^\s#]+)')) {
        $target = ($m.Groups[1].Value.Split('/')[0..1] -join '/').ToLowerInvariant(); $path = $m.Groups[1].Value.Substring($target.Length + 1); $ref = $m.Groups[2].Value; $key = "$target/$path@$ref"; if ($Allowed -notcontains $target) {
            Add-LdcCollectorGap GitHub $Scope $Resource 'OFF_SCOPE_REFERENCE_NOT_FOLLOWED'; continue
        }; if ($ref -match '\$') {
            Add-LdcCollectorGap GitHub $Scope $Resource 'DYNAMIC_REFERENCE_UNRESOLVED'; continue
        }; if ($Depth -ge (Get-LdcLimit maxReferenceDepth 8)) {
            Add-LdcCollectorGap GitHub $Scope $key 'REFERENCE_DEPTH_LIMIT_REACHED'; continue
        }; if ($script:Ldc.RepositoryReferences.Count -ge (Get-LdcLimit maxReferenceFiles 100)) {
            Add-LdcCollectorGap GitHub $Scope $key 'REFERENCE_LIMIT_REACHED'; continue
        }; if ($script:Ldc.RepositoryReferences.Add($key)) {
            Add-LdcTextReferences GitHub $target $key (Get-LdcGitHubText $target $path $ref) $target '' $target $Allowed ($Depth + 1)
        }
    }
    foreach ($m in [regex]::Matches($Text, '(?im)^\s*uses:\s*\.\/([^\s#]+)')) {
        $path = $m.Groups[1].Value.TrimStart('.', '/'); $ref = ($Resource -split '@')[-1]; $key = "$Repository/$path@$ref"; if ($path -match '\$') {
            Add-LdcCollectorGap GitHub $Scope $Resource 'DYNAMIC_REFERENCE_UNRESOLVED'; continue
        }; if ($Depth -ge (Get-LdcLimit maxReferenceDepth 8)) {
            Add-LdcCollectorGap GitHub $Scope $key 'REFERENCE_DEPTH_LIMIT_REACHED'; continue
        }; if ($script:Ldc.RepositoryReferences.Add($key)) {
            Add-LdcTextReferences GitHub $Repository $key (Get-LdcGitHubText $Repository $path $ref) $Repository '' $Repository $Allowed ($Depth + 1)
        }
    }
}
function Invoke-LdcGitHubRepository {
    param($Repo, $Meta, [string[]]$Allowed)
    $branch = [string](Get-LdcValue $Meta default_branch ''); if (!$branch) {
        Add-LdcCollectorGap GitHub $Repo $Repo 'DEFAULT_BRANCH_UNAVAILABLE'; return
    }; Add-LdcFinding -Platform GitHub -Scope $Repo -Resource $Repo -Classification ResourceInventory -Application $Repo -Evidence 'Repository metadata only.' -NextStep 'Review default-branch sources.'; try {
        $tree = (Invoke-LdcRequest -Platform GitHub -Uri "https://api.github.com/repos/$Repo/git/trees/$([uri]::EscapeDataString($branch))?recursive=1").Data
    }
    catch {
        Add-LdcCollectorGap GitHub $Repo $Repo 'TREE_UNAVAILABLE'; $tree = @{tree = @() }
    }; if (Get-LdcValue $tree truncated $false) {
        Add-LdcCollectorGap GitHub $Repo "$Repo@$branch" 'TREE_TRUNCATED'
    }; $ext = @('.yml', '.yaml', '.json', '.toml', '.ini', '.config', '.xml', '.properties', '.env', '.js', '.jsx', '.ts', '.tsx', '.py', '.cs', '.fs', '.go', '.java', '.rb', '.tf', '.tfvars', '.hcl', '.ps1', '.psm1', '.sh', '.bash'); $sourceFiles = @(Get-LdcValue $tree tree @() | Where-Object { $path = [string](Get-LdcValue $_ path ''); (Get-LdcValue $_ type '') -eq 'blob' -and (($path -like '*.env' -or [IO.Path]::GetFileName($path) -in @('.terraformrc','terraform.rc')) -or ([IO.Path]::GetExtension($path).ToLowerInvariant() -in $ext)) }); if ($sourceFiles.Count -gt (Get-LdcLimit maxSourceFiles 1000)) {
        Add-LdcCollectorGap GitHub $Repo "$Repo@$branch" 'SOURCE_FILE_LIMIT_REACHED'
    }; foreach ($f in @($sourceFiles | Select-Object -First (Get-LdcLimit maxSourceFiles 1000))) {
        $path = [string](Get-LdcValue $f path ''); if (([int64](Get-LdcValue $f size 0)) -gt (Get-LdcLimit maxContentCharacters 1048576)) {
            Add-LdcCollectorGap GitHub $Repo "$Repo/$path@$branch" 'CONTENT_LIMIT_REACHED'; continue
        }; Add-LdcTextReferences GitHub $Repo "$Repo/$path@$branch" (Get-LdcGitHubText $Repo $path $branch) $Repo '' $Repo $Allowed
    }; foreach ($s in Get-LdcPaged GitHub "https://api.github.com/repos/$Repo/actions/secrets") {
        Add-LdcFinding -Platform GitHub -Scope $Repo -Resource "$Repo/secret/$([string](Get-LdcValue $s name 'secret'))" -Classification SecretMetadata -Application $Repo -Evidence 'Repository secret metadata; masked value not read.' -NextStep 'Confirm authorized inventory.'; Add-LdcGap -Platform GitHub -Scope $Repo -Resource "$Repo/secret/$([string](Get-LdcValue $s name 'secret'))" -Reason 'MASKED_VARIABLE_VALUE_UNAVAILABLE' -NextStep 'Compare the named secret through the authorized inventory.'
    }; foreach ($e in Get-LdcPaged GitHub "https://api.github.com/repos/$Repo/environments") {
        $n = [string](Get-LdcValue $e name 'environment'); Add-LdcFinding -Platform GitHub -Scope $Repo -Resource "$Repo/environment/$n" -Classification ResourceInventory -Application $Repo -Environment $n -Evidence 'Environment metadata only.' -NextStep 'Review environment secrets.'; foreach ($s in Get-LdcPaged GitHub "https://api.github.com/repos/$Repo/environments/$([uri]::EscapeDataString($n))/secrets") {
            Add-LdcFinding -Platform GitHub -Scope $Repo -Resource "$Repo/environment/$n/secret/$([string](Get-LdcValue $s name 'secret'))" -Classification SecretMetadata -Application $Repo -Environment $n -Evidence 'Environment secret metadata; masked value not read.' -NextStep 'Confirm authorized inventory.'; Add-LdcGap -Platform GitHub -Scope $Repo -Resource "$Repo/environment/$n/secret/$([string](Get-LdcValue $s name 'secret'))" -Reason 'MASKED_VARIABLE_VALUE_UNAVAILABLE' -NextStep 'Compare the named secret through the authorized inventory.'
        }
    }; foreach ($w in Get-LdcPaged GitHub "https://api.github.com/repos/$Repo/actions/workflows") {
        $path = [string](Get-LdcValue $w path 'workflow'); Add-LdcFinding -Platform GitHub -Scope $Repo -Resource "$Repo/workflow/$path" -Classification PipelineInventory -Application $Repo -Evidence 'Workflow metadata; default-branch source scan attempted.' -NextStep 'Review workflow references.'; if ($path) {
            Add-LdcTextReferences GitHub $Repo "$Repo/$path@$branch" (Get-LdcGitHubText $Repo $path $branch) $Repo '' $Repo $Allowed
        }
    }
}
function Invoke-LdcAdoText {
    param($Base, $Repo, $Path, $Branch, $Scope, [ValidateSet('branch','tag','commit')][string]$VersionType = 'branch') try {
        if ($Branch.StartsWith('refs/heads/')) { $Branch = $Branch.Substring(11) }
        ConvertFrom-LdcContent (Invoke-LdcRequest -Platform AzureDevOps -Uri "$Base/git/repositories/$([uri]::EscapeDataString($Repo))/items?path=$([uri]::EscapeDataString($Path))&versionDescriptor.version=$([uri]::EscapeDataString($Branch))&versionDescriptor.versionType=$VersionType&includeContent=true&api-version=7.1" -Raw) AzureDevOps
    }
    catch {
        Add-LdcCollectorGap AzureDevOps $Scope "$Repo/$Path@$Branch" 'CONTENT_UNAVAILABLE'; $null
    }
}
function Invoke-LdcAdoTextReferences {
    param($Base, $Scope, $Repository, $Path, $Branch, $Application, [int]$Depth = 0,
        [hashtable]$InheritedAliases = @{}, [string]$VersionType = 'branch')
    if (-not $script:Ldc.AdoTemplateReferences) {
        $script:Ldc.AdoTemplateReferences = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        $script:Ldc.AdoTemplateCount = 0
    }
    $key = "$Base/$Repository/$Path@$VersionType/$Branch"
    if ($Depth -ge 8) { Add-LdcCollectorGap AzureDevOps $Scope $key 'REFERENCE_DEPTH_LIMIT_REACHED'; return }
    if ($Depth -gt 0 -and $script:Ldc.AdoTemplateCount -ge 100) { Add-LdcCollectorGap AzureDevOps $Scope $key 'REFERENCE_LIMIT_REACHED'; return }
    if (-not $script:Ldc.AdoTemplateReferences.Add($key)) { return }
    if ($Depth -gt 0) { $script:Ldc.AdoTemplateCount++ }
    $text = Invoke-LdcAdoText $Base $Repository $Path $Branch $Scope $VersionType
    if ($null -eq $text) { return }
    Add-LdcTextReferences AzureDevOps $Scope $key $text $Application '' $Repository @()
    if ($text.Length -gt (Get-LdcLimit maxContentCharacters 1048576)) { return }
    if ([IO.Path]::GetExtension($Path) -notin @('.yml', '.yaml')) { return }

    # Static YAML subset only. Keep aliases from the originating pipeline when
    # traversing its templates. Unsupported expressions never become fetch targets.
    $aliases = $InheritedAliases.Clone()
    if ($Depth -eq 0) { $aliases['self'] = @{Name = $Repository; Ref = $Branch; 'Type' = 'git'; Base = $Base; Scope = $Scope} }
    $lines = $text -split '\r?\n'
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -notmatch '^(\s*)-\s*repository:\s*([A-Za-z0-9_.-]+)\s*(?:#.*)?$') { continue }
        $indent = $Matches[1].Length; $alias = $Matches[2]
        $resource = @{Name = ''; Ref = 'refs/heads/main'; Type = ''; Base = $Base; Scope = $Scope}
        for ($j = $i + 1; $j -lt $lines.Count; $j++) {
            if (!$lines[$j].Trim() -or $lines[$j].TrimStart().StartsWith('#')) { continue }
            $spaces = $lines[$j].Length - $lines[$j].TrimStart().Length
            if ($spaces -le $indent) { break }
            if ($lines[$j] -match '^\s*(name|ref|type):\s*["'']?([^"''#]+?)["'']?\s*(?:#.*)?$') {
                $resource[$Matches[1]] = $Matches[2].Trim()
            }
        }
        $aliases[$alias] = $resource
    }
    foreach ($match in [regex]::Matches($text, '(?m)^\s*(?:-\s*)?template:\s*([^\r\n#]+)')) {
        $reference = $match.Groups[1].Value.Trim().Trim('"', "'")
        if ($reference -match '\$|[{}\[\]]') { Add-LdcCollectorGap AzureDevOps $Scope $key 'DYNAMIC_REFERENCE_UNRESOLVED'; continue }
        $parts = $reference -split '@', 2; $templatePath = $parts[0]
        $targetRepo = $Repository; $targetBranch = $Branch; $targetBase = $Base; $targetScope = $Scope; $targetType = $VersionType
        if ($parts.Count -eq 2) {
            if (!$aliases.ContainsKey($parts[1])) { Add-LdcCollectorGap AzureDevOps $Scope $key 'UNSUPPORTED_TEMPLATE_REPOSITORY_REFERENCE'; continue }
            $resource = $aliases[$parts[1]]
            if ($resource.Type -ne 'git' -or $resource.Name -match '[$:{}\\]' -or $resource.Ref -match '[$:{}\s]') {
                Add-LdcCollectorGap AzureDevOps $Scope $key 'UNSUPPORTED_TEMPLATE_REPOSITORY_REFERENCE'; continue
            }
            $targetBase = $resource.Base; $targetScope = $resource.Scope
            $repoParts = $resource.Name -split '/'
            if ($repoParts.Count -eq 1) { $targetRepo = $repoParts[0] }
            elseif ($repoParts.Count -eq 2) {
                $org = ($resource.Scope -split '/', 2)[0]; $project = $repoParts[0]; $targetRepo = $repoParts[1]
                $targetScope = "$org/$project"
                $targetBase = "https://dev.azure.com/$([uri]::EscapeDataString($org))/$([uri]::EscapeDataString($project))/_apis"
            } else { Add-LdcCollectorGap AzureDevOps $Scope $key 'UNSUPPORTED_TEMPLATE_REPOSITORY_REFERENCE'; continue }
            $targetType = 'branch'; $targetBranch = $resource.Ref
            if ($targetBranch.StartsWith('refs/heads/')) { $targetBranch = $targetBranch.Substring(11) }
            elseif ($targetBranch.StartsWith('refs/tags/')) { $targetBranch = $targetBranch.Substring(10); $targetType = 'tag' }
            elseif ($targetBranch -match '^[a-fA-F0-9]{40}$') { $targetType = 'commit' }
        }
        if ($templatePath.StartsWith('/') -or $parts.Count -eq 2) { $combined = $templatePath }
        else { $combined = ($Path -replace '[^/]+$', '') + $templatePath }
        $segments = [Collections.Generic.List[string]]::new(); $invalid = $false
        foreach ($segment in $combined.Split('/')) {
            if ($segment -in @('', '.')) { continue }
            if ($segment -eq '..') {
                if (!$segments.Count) { $invalid = $true; break }
                $segments.RemoveAt($segments.Count - 1)
            } else { $segments.Add($segment) }
        }
        if ($invalid -or !$segments.Count) { Add-LdcCollectorGap AzureDevOps $Scope $key 'INVALID_TEMPLATE_PATH'; continue }
        $resolvedPath = '/' + ($segments -join '/')
        try { Assert-LdcRequest AzureDevOps ([uri]"$targetBase/git/repositories/$([uri]::EscapeDataString($targetRepo))/items") GET }
        catch { Add-LdcCollectorGap AzureDevOps $Scope $key 'OFF_SCOPE_REFERENCE_NOT_FOLLOWED'; continue }
        Invoke-LdcAdoTextReferences $targetBase $targetScope $targetRepo $resolvedPath $targetBranch $Application ($Depth + 1) $aliases $targetType
    }
}
function Invoke-LdcAdoRepository {
    param($Base, $Scope, $Repo)$name = [string](Get-LdcValue $Repo name 'repository'); $id = [string](Get-LdcValue $Repo id $name); $b = [string](Get-LdcValue $Repo defaultBranch ''); if ($b.StartsWith('refs/heads/')) {
        $b = $b.Substring(11)
    }; Add-LdcFinding -Platform AzureDevOps -Scope $Scope -Resource "$Scope/repository/$name" -Classification ResourceInventory -Application "$Scope/$name" -Evidence 'Repository metadata only.' -NextStep 'Inspect default-branch source.'; if (!$b) {
        Add-LdcCollectorGap AzureDevOps $Scope "$Scope/repository/$name" 'DEFAULT_BRANCH_UNAVAILABLE'; return
    }
    $listUri = "$Base/git/repositories/$([uri]::EscapeDataString($id))/items?recursionLevel=Full&includeContentMetadata=true&versionDescriptor.version=$([uri]::EscapeDataString($b))&versionDescriptor.versionType=branch&api-version=7.1"
    try { $items = (Invoke-LdcRequest -Platform AzureDevOps -Uri $listUri).Data.value } catch { Add-LdcCollectorGap AzureDevOps $Scope "$Scope/repository/$name@$b" 'ITEMS_LIST_UNAVAILABLE'; return }
    if ($items -isnot [array]) { Add-LdcCollectorGap AzureDevOps $Scope "$Scope/repository/$name@$b" 'INVALID_ITEMS_LIST'; return }
    $extensions = @('.yml','.yaml','.json','.toml','.ini','.config','.xml','.properties','.env','.js','.jsx','.ts','.tsx','.py','.cs','.fs','.go','.java','.rb','.tf','.tfvars','.hcl','.ps1','.psm1','.sh','.bash')
    $eligible = @($items | Where-Object { -not (Get-LdcValue $_ isFolder $false) -and (([IO.Path]::GetExtension([string](Get-LdcValue $_ path '')).ToLowerInvariant() -in $extensions) -or ([IO.Path]::GetFileName([string]$_.path) -in @('.env','.terraformrc','terraform.rc'))) })
    if ($eligible.Count -gt 1000) { Add-LdcCollectorGap AzureDevOps $Scope "$Scope/repository/$name@$b" 'SOURCE_FILE_LIMIT_REACHED' }
    foreach ($item in @($eligible | Select-Object -First 1000)) { $path = [string](Get-LdcValue $item path ''); Invoke-LdcAdoTextReferences $Base $Scope $id $path $b "$Scope/$name" }
}
function Invoke-LdcAdoDefinition {
    param($Base, $Scope, $Definition)$id = [string](Get-LdcValue $Definition id ''); $name = [string](Get-LdcValue $Definition name 'definition'); try {
        $d = (Invoke-LdcRequest -Platform AzureDevOps -Uri "$Base/build/definitions/${id}?api-version=7.1").Data
    }
    catch {
        Add-LdcCollectorGap AzureDevOps $Scope "$Scope/build/$name" 'BUILD_DEFINITION_UNAVAILABLE'; return
    }
    Add-LdcFinding -Platform AzureDevOps -Scope $Scope -Resource "$Scope/build/$name" -Classification PipelineInventory -Application $Scope -Owner ([string]$d.authoredBy.displayName) -Evidence 'Build definition and available static configuration.'
    Add-LdcAdoVariables $Scope "$Scope/build/$name" $d.variables ''
    Add-LdcAdoTaskReferences $Scope "$Scope/build/$name" $d.process ''
    $process = Get-LdcValue $d process @{}; $path = [string](Get-LdcValue $process yamlFilename ''); $repo = Get-LdcValue $d repository @{}; $branch = [string](Get-LdcValue $repo defaultBranch ''); if (!$path -or !$branch) {
        Add-LdcCollectorGap AzureDevOps $Scope "$Scope/build/$name" 'DEFAULT_YAML_BRANCH_UNAVAILABLE'; return
    }; $branch = $branch -replace '^refs/heads/', ''; Invoke-LdcAdoTextReferences $Base $Scope ([string](Get-LdcValue $repo id '')) $path $branch $Scope
}
function Invoke-LdcAdoVariableGroup {
    param($Base, $Scope, $Group)$id = [string](Get-LdcValue $Group id ''); $name = [string](Get-LdcValue $Group name 'group'); try {
        $d = (Invoke-LdcRequest -Platform AzureDevOps -Uri "$Base/distributedtask/variablegroups/${id}?api-version=7.1").Data
    }
    catch {
        Add-LdcCollectorGap AzureDevOps $Scope "$Scope/variable-group/$name" 'VARIABLE_GROUP_UNAVAILABLE'; return
    }
    Add-LdcAdoVariables $Scope "$Scope/variable-group/$name" $d.variables ''
    if ($d.type -ne 'AzureKeyVault') { return }
    $vaultName = [string]$d.providerData.vault
    if ($vaultName -notmatch '^[A-Za-z0-9-]{3,24}$' -or $d.variables -isnot [Collections.IDictionary]) {
        Add-LdcCollectorGap AzureDevOps $Scope "$Scope/variable-group/$name" 'KEYVAULT_VARIABLE_MAPPING_UNAVAILABLE'; return
    }
    foreach ($variableName in $d.variables.Keys) {
        if ($variableName -notmatch '^[A-Za-z0-9-]+$') { Add-LdcCollectorGap AzureDevOps $Scope "$Scope/variable-group/$name" 'INVALID_KEYVAULT_VARIABLE_NAME'; continue }
        Add-LdcFinding -Platform AzureDevOps -Scope $Scope -Resource "$Scope/variable-group/$name/variable/$variableName" -Classification UnresolvedCandidate -Application $Scope -SecretReference ("https://$vaultName.vault.azure.net/secrets/$variableName").ToLowerInvariant() -Owner ([string]$d.createdBy.displayName) -Evidence 'Azure Key Vault variable-group mapping; unversioned reference.' -NextStep 'Verify the currently resolved version and the pipelines using this group.'
    }
}
function Invoke-LdcAdoRelease {
    param($Org, $Project, $Scope, $Release)$id = [string](Get-LdcValue $Release id ''); $name = [string](Get-LdcValue $Release name 'release'); try {
        $d = (Invoke-LdcRequest -Platform AzureDevOps -Uri "https://vsrm.dev.azure.com/$Org/$Project/_apis/release/definitions/${id}?api-version=7.1").Data
    }
    catch {
        Add-LdcCollectorGap AzureDevOps $Scope "$Scope/release/$name" 'RELEASE_DEFINITION_UNAVAILABLE'; return
    }
    Add-LdcFinding -Platform AzureDevOps -Scope $Scope -Resource "$Scope/release/$name" -Classification PipelineInventory -Application $Scope -Owner ([string]$d.createdBy.displayName) -Evidence 'Classic release configuration inventory; bodies withheld.'
    Add-LdcAdoVariables $Scope "$Scope/release/$name" $d.variables ''
    foreach ($environment in @(Get-LdcValue $d environments @())) {
        $environmentName = [string](Get-LdcValue $environment name 'environment')
        Add-LdcAdoVariables $Scope "$Scope/release/$name/environment/$environmentName" $environment.variables $environmentName
        Add-LdcAdoTaskReferences $Scope "$Scope/release/$name/environment/$environmentName" $environment.deployPhases $environmentName
    }
}
function Add-LdcAdoVariables {
    param($Scope, $Resource, $Variables, $Environment)
    if ($null -eq $Variables) { return }
    if ($Variables -isnot [Collections.IDictionary]) { Add-LdcCollectorGap AzureDevOps $Scope $Resource 'INVALID_VARIABLE_COLLECTION'; return }
    foreach ($entry in $Variables.GetEnumerator()) {
        $resourceName = "$Resource/variable/$($entry.Key)"
        $value = Get-LdcValue $entry.Value value $null
        if ($value -is [string]) { Register-LdcSensitive $value }
        Add-LdcFinding -Platform AzureDevOps -Scope $Scope -Resource $resourceName -Classification SecretMetadata -Environment $Environment -Evidence 'Variable name; values withheld.' -NextStep 'Trace the variable to its deployment owner.'
        if ($null -eq $value) {
            Add-LdcCollectorGap AzureDevOps $Scope $resourceName 'MASKED_VARIABLE_VALUE_UNAVAILABLE'
        } elseif ($value -is [string]) {
            if (Test-LdcSecret $value) {
                Add-LdcFinding -Platform AzureDevOps -Scope $Scope -Resource $resourceName -Classification ExactStoredKeyMatch -Application $Resource -Environment $Environment -SecretReference $resourceName -Evidence 'Exact readable variable value match; current response only, value withheld.'
            }
            Add-LdcTextReferences AzureDevOps $Scope $resourceName $value $Resource $Environment '' @()
        } else { Add-LdcCollectorGap AzureDevOps $Scope $resourceName 'INVALID_VARIABLE_VALUE' }
    }
}
function Add-LdcAdoTaskReferences {
    param($Scope, $Resource, $Object, $Environment, [int]$Depth = 0)
    if ($null -eq $Object) { return }
    if ($Depth -gt 12) { Add-LdcCollectorGap AzureDevOps $Scope $Resource 'TASK_REFERENCE_DEPTH_LIMIT'; return }
    if ($Object -is [Collections.IDictionary]) {
        if ($Object.Contains('inputs')) {
            Add-LdcFinding -Platform AzureDevOps -Scope $Scope -Resource $Resource -Classification TaskReference -Environment $Environment -Evidence 'Task input references inspected in memory; bodies withheld.'
            if ($Object.inputs -is [Collections.IDictionary]) {
                foreach ($inputValue in $Object.inputs.GetEnumerator()) {
                    if ($inputValue.Value -is [string]) {
                        Add-LdcTextReferences AzureDevOps $Scope "$Resource/input/$($inputValue.Key)" $inputValue.Value $Resource $Environment '' @()
                    }
                }
            } else { Add-LdcCollectorGap AzureDevOps $Scope $Resource 'INVALID_TASK_INPUTS' }
        }
        foreach ($property in $Object.GetEnumerator()) {
            if ($property.Key -in @('inputs','variables','authorization')) { continue }
            if ($property.Value -is [Collections.IDictionary] -or $property.Value -is [array]) {
                Add-LdcAdoTaskReferences $Scope "$Resource/$($property.Key)" $property.Value $Environment ($Depth + 1)
            }
        }
    } elseif ($Object -is [array]) {
        for ($index = 0; $index -lt $Object.Count; $index++) { Add-LdcAdoTaskReferences $Scope "$Resource/$index" $Object[$index] $Environment ($Depth + 1) }
    }
}
function Invoke-LdcGitHubOrganizationSecrets {
    param([string] $Organization)
    foreach ($secret in Get-LdcPaged GitHub "https://api.github.com/orgs/$([uri]::EscapeDataString($Organization))/actions/secrets") {
        $name = [string](Get-LdcValue $secret name 'secret')
        $visibility = [string](Get-LdcValue $secret visibility 'unknown'); Add-LdcFinding -Platform GitHub -Scope $Organization -Resource "$Organization/organization-secret/$name" -Classification SecretMetadata -Application $Organization -Evidence "Organization secret metadata; visibility=$visibility; masked value not read." -NextStep 'Confirm organization secret visibility and selected-repository scope.'; Add-LdcGap -Platform GitHub -Scope $Organization -Resource "$Organization/organization-secret/$name" -Reason 'MASKED_VARIABLE_VALUE_UNAVAILABLE' -NextStep 'Compare the named secret through the authorized inventory.'
        if ($visibility -ne 'selected') { continue }
        try {
            foreach ($repo in Get-LdcPaged GitHub "https://api.github.com/orgs/$([uri]::EscapeDataString($Organization))/actions/secrets/$([uri]::EscapeDataString($name))/repositories") {
                $fullName = [string](Get-LdcValue $repo full_name (Get-LdcValue $repo name 'repository'))
                Add-LdcFinding -Platform GitHub -Scope $Organization -Resource "$Organization/organization-secret/$name/repository/$fullName" -Classification SecretScope -Application $Organization -Evidence 'Organization secret selected-repository scope metadata.' -NextStep 'Confirm the selected repository can consume this secret.'
            }
        } catch { Add-LdcCollectorGap GitHub $Organization "$Organization/organization-secret/$name" 'ORGANIZATION_SECRET_SCOPE_UNAVAILABLE' }
    }
}
function Invoke-LdcRepositories {
    $c = $script:Ldc.Config; $gh = Get-LdcValue $c github @{}; $allowed = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase); foreach ($r in @(Get-LdcValue $gh repositories @())) {
        if ($r -match '^[^/\s]+/[^/\s]+$') {
            [void]$allowed.Add($r)
        }
        else {
            Add-LdcCollectorGap GitHub configuration github.repositories 'INVALID_SCOPE_CONFIGURATION'
        }
    }; foreach ($org in @(Get-LdcValue $gh organizations @())) {
        Invoke-LdcScope GitHub $org { Invoke-LdcGitHubOrganizationSecrets $org; foreach ($r in Get-LdcPaged GitHub "https://api.github.com/orgs/$org/repos") {
                $name = [string](Get-LdcValue $r full_name ''); if ($name) {
                    [void]$allowed.Add($name)
                }
            } }
    }; foreach ($r in $allowed) {
        Invoke-LdcScope GitHub $r { try {
                Invoke-LdcGitHubRepository $r (Invoke-LdcRequest -Platform GitHub -Uri "https://api.github.com/repos/${r}?per_page=1").Data ([string[]]@($allowed))
            }
            catch {
                Add-LdcCollectorGap GitHub $r $r 'REPOSITORY_METADATA_UNAVAILABLE'
            } }
    }; foreach ($a in @(Get-LdcValue $c azureDevOps @())) {
        $o = [string](Get-LdcValue $a organization ''); foreach ($p in @(Get-LdcValue $a projects @())) {
            if (!$o -or !$p) {
                Add-LdcCollectorGap AzureDevOps configuration azureDevOps 'INVALID_SCOPE_CONFIGURATION'; continue
            }; $scope = "$o/$p"; $base = "https://dev.azure.com/$o/$p/_apis"; Invoke-LdcScope AzureDevOps "$scope/repositories" { foreach ($r in Get-LdcPaged AzureDevOps "$base/git/repositories?api-version=7.1") {
                    Invoke-LdcAdoRepository $base $scope $r
                } }; Invoke-LdcScope AzureDevOps "$scope/builds" { foreach ($d in Get-LdcPaged AzureDevOps "$base/build/definitions?api-version=7.1") {
                    Invoke-LdcAdoDefinition $base $scope $d
                } }; Invoke-LdcScope AzureDevOps "$scope/variable-groups" { foreach ($g in Get-LdcPaged AzureDevOps "$base/distributedtask/variablegroups?api-version=7.1") {
                    Invoke-LdcAdoVariableGroup $base $scope $g
                } }; Invoke-LdcScope AzureDevOps "$scope/releases" { $u = "https://vsrm.dev.azure.com/$o/$p/_apis/release/definitions?api-version=7.1"; foreach ($r in Get-LdcPaged AzureDevOps $u) {
                    Invoke-LdcAdoRelease $o $p $scope $r
                } }
        }
    }
}
