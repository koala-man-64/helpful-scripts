function Read-LdcConfiguration {
    param([string]$Path, [ValidateSet('LaunchDarkly','TerraformCloud')][string]$CredentialKind = 'LaunchDarkly')
    try {
        $file = Get-Item -LiteralPath $Path -ErrorAction Stop
        if ($file.Length -gt 1MB) { throw 'size' }
        $config = ConvertFrom-Json -AsHashtable -InputObject ([IO.File]::ReadAllText($file.FullName)) -ErrorAction Stop
        if ($config -isnot [Collections.IDictionary]) { throw 'shape' }
        $providerField = if ($CredentialKind -eq 'TerraformCloud') { 'terraformCloud' } else { 'launchDarkly' }
        foreach ($key in $config.Keys) {
            if ($key -notin @($providerField,'azure','gcp','azureDevOps','github')) { throw 'field' }
        }
        if ($config[$providerField] -isnot [Collections.IDictionary]) { throw 'provider' }
        if ($CredentialKind -eq 'LaunchDarkly') {
            foreach ($field in @('projectKey','environmentKey','credentialResourceKey')) {
                if ($config.launchDarkly[$field] -isnot [string] -or $config.launchDarkly[$field] -notmatch '^[A-Za-z0-9_.-]{1,256}$') { throw 'launchdarkly-field' }
            }
            foreach ($field in $config.launchDarkly.Keys) {
                if ($field -notin @('projectKey','environmentKey','credentialResourceKey')) { throw 'field' }
            }
        } else {
            foreach ($field in $config.terraformCloud.Keys) { if ($field -notin @('hostname','organizations')) { throw 'field' } }
            if (!$config.terraformCloud.Contains('hostname')) { $config.terraformCloud.hostname = 'app.terraform.io' }
            if ($config.terraformCloud.hostname -cnotin @('app.terraform.io','app.eu.terraform.io')) { throw 'terraform-host' }
            if ($config.terraformCloud.organizations -isnot [array]) { throw 'terraform-organizations' }
            foreach ($org in $config.terraformCloud.organizations) {
                if ($org -isnot [string] -or $org -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$') { throw 'terraform-organization' }
            }
            $config.terraformCloud.organizations = @($config.terraformCloud.organizations | Select-Object -Unique)
        }
        foreach ($name in @('azure','gcp','github')) {
            if (-not $config.ContainsKey($name)) { $config[$name]=@{} }
            if ($config[$name] -isnot [Collections.IDictionary]) { throw 'scope-object' }
        }
        foreach ($spec in @(@('azure','subscriptionIds'),@('gcp','projectIds'),@('github','organizations'),@('github','repositories'))) {
            $group=$spec[0]; $field=$spec[1]
            if (-not $config[$group].ContainsKey($field)) { $config[$group][$field]=@() }
            if ($config[$group][$field] -isnot [array]) { throw 'scope-array' }
            foreach ($value in $config[$group][$field]) {
                if ($value -isnot [string]) { throw 'scope-string' }
                $pattern = switch ($field) {
                    'subscriptionIds' { '^[a-fA-F0-9]{8}-(?:[a-fA-F0-9]{4}-){3}[a-fA-F0-9]{12}$' }
                    'projectIds' { '^(?:[a-z][a-z0-9-]{4,28}[a-z0-9]|[0-9]{6,20})$' }
                    'organizations' { '^[A-Za-z0-9][A-Za-z0-9-]{0,38}$' }
                    'repositories' { '^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}$' }
                }
                if ($value -notmatch $pattern -or $value -match '/\.\.?$') { throw 'scope-format' }
            }
            $config[$group][$field]=@($config[$group][$field] | Select-Object -Unique)
        }
        foreach ($name in @('azure','gcp','github')) {
            $allowed=switch ($name) { 'azure' {@('subscriptionIds')} 'gcp' {@('projectIds')} 'github' {@('organizations','repositories')} }
            foreach ($key in $config[$name].Keys) { if ($key -notin $allowed) { throw 'scope-field' } }
        }
        if (-not $config.ContainsKey('azureDevOps')) { $config.azureDevOps=@() }
        if ($config.azureDevOps -isnot [array]) { throw 'devops-array' }
        foreach ($org in $config.azureDevOps) {
            if ($org -isnot [Collections.IDictionary] -or $org.organization -isnot [string] -or $org.organization -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,49}$' -or $org.projects -isnot [array] -or -not $org.projects.Count) { throw 'devops-scope' }
            foreach ($key in $org.Keys) { if ($key -notin @('organization','projects')) { throw 'devops-field' } }
            foreach ($project in $org.projects) {
                if ($project -isnot [string] -or $project -notmatch '^[A-Za-z0-9][A-Za-z0-9 _.()-]{0,127}$') { throw 'devops-project' }
            }
        }
        return $config
    } catch { throw 'LDC:invalid-configuration' }
}

function Read-LdcPrivateValue {
    param([string]$EnvironmentVariable, [string]$Prompt)
    $value=[Environment]::GetEnvironmentVariable($EnvironmentVariable,'Process')
    if ($value) { return $value }
    $secure=Read-Host -Prompt $Prompt -AsSecureString
    $pointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer); $secure.Dispose() }
}
