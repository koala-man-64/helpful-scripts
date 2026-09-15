# Private implementation for Find-LaunchDarklyConsumers.ps1. No work on import.
function Initialize-LdcState {
    param([hashtable]$Config, [string]$KnownSecret, [string]$ManagementToken,
        [datetimeoffset]$From = [datetimeoffset]::UtcNow.AddDays(-30), [datetimeoffset]$To = [datetimeoffset]::UtcNow)
    $script:Ldc = @{
        Config = $Config; KnownSecret = $KnownSecret; ManagementToken = $ManagementToken
        From = $From; To = $To; MaxPages = 10000; MaxRetries = 4; TimeoutSeconds = 30
        Findings = [Collections.Generic.List[object]]::new(); Coverage = [Collections.Generic.List[object]]::new()
        Sensitive = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        AllowedVaultHosts = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        Tokens = @{}; GapCount = 0; GcpProjectAliases = @{}; SensitiveCharacters = 0
    }
    Register-LdcSensitive $KnownSecret
    Register-LdcSensitive $ManagementToken
}

function Register-LdcSensitive {
    param([AllowNull()][AllowEmptyString()][string]$Value)
    if ($Value -and -not $script:Ldc.Sensitive.Contains($Value)) {
        if ($script:Ldc.SensitiveCharacters + $Value.Length -gt 32MB) { throw 'LDC:sensitive-memory-limit' }
        [void]$script:Ldc.Sensitive.Add($Value)
        $script:Ldc.SensitiveCharacters += $Value.Length
    }
}

function Test-LdcSecret {
    param([AllowNull()][AllowEmptyString()][string]$Value)
    Register-LdcSensitive $Value
    return [string]::Equals($Value, $script:Ldc.KnownSecret, [StringComparison]::Ordinal)
}

function Protect-LdcText {
    param([AllowNull()][object]$Value)
    $text = [string]$Value
    # Redact before truncating so prefixes of credentials cannot survive truncation.
    foreach ($secret in $script:Ldc.Sensitive) {
        $text = $text.Replace($secret, '[REDACTED]', [StringComparison]::Ordinal)
    }
    $text = [regex]::Replace($text, '(?i)\b(?:sdk-[a-z0-9-]+|(?:gh[pousr]_|github_pat_)[a-z0-9_]+|eyJ[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+)\b', '[REDACTED]')
    $text = [regex]::Replace($text, '[\x00-\x1f\x7f]', ' ')
    if ($text.Length -gt 2048) { $text = $text.Substring(0, 2048) + '[TRUNCATED]' }
    return $text
}

function Add-LdcFinding {
    param([string]$Platform, [string]$Scope, [string]$Resource, [string]$Classification,
        [string]$Application, [string]$Environment, [string]$SecretReference, [string]$SecretVersion,
        [string]$Owner, [string]$FirstObservedBucket, [string]$LastObservedBucket,
        [string]$Evidence, [string]$NextStep)
    # Fixed fields only; never serialize a provider object or raw configuration body.
    $row = [ordered]@{}
    foreach ($name in @('Platform','Scope','Resource','Classification','Application','Environment',
        'SecretReference','SecretVersion','Owner','FirstObservedBucket','LastObservedBucket','Evidence','NextStep')) {
        $row[$name] = [string](Get-Variable -Name $name -ValueOnly)
    }
    $script:Ldc.Findings.Add([pscustomobject]$row)
}

function Add-LdcGap {
    param([string]$Platform, [string]$Scope, [string]$Resource, [string]$Reason, [string]$NextStep = 'Resolve this gap and rerun the explicit scope.')
    $script:Ldc.GapCount++
    Add-LdcFinding -Platform $Platform -Scope $Scope -Resource $Resource -Classification CoverageGap -Evidence $Reason -NextStep $NextStep
    if ($Reason -match '(?i)^unsupported') {
        $script:Ldc.Coverage.Add([ordered]@{Platform=$Platform;Scope=$Resource;Status='unsupported';Reason=$Reason})
    }
}

function Get-LdcFailureCode {
    param($ErrorRecord)
    $message = [string]$ErrorRecord.Exception.Message
    if ($message -match '^LDC:([a-z0-9-]+)$') { return $Matches[1] }
    return 'collection-failed'
}

function Invoke-LdcScope {
    param([string]$Platform, [string]$Scope, [scriptblock]$Action)
    $entry = [ordered]@{ Platform=$Platform; Scope=$Scope; Status='requested'; Reason='' }
    $script:Ldc.Coverage.Add($entry)
    $before = $script:Ldc.GapCount
    try {
        & $Action | Out-Null
        $entry.Status = if ($script:Ldc.GapCount -gt $before) { 'partial' } else { 'scanned' }
    } catch {
        $entry.Status = 'failed'; $entry.Reason = Get-LdcFailureCode $_
        Add-LdcGap -Platform $Platform -Scope $Scope -Resource $Scope -Reason $entry.Reason
    }
}

function Invoke-LdcNative {
    param([ValidateSet('az','gcloud','gh')][string]$Command, [string[]]$Arguments)
    $resolved = Get-Command $Command -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $resolved) { throw "LDC:missing-$Command" }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.UseShellExecute = $false; $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true; $start.RedirectStandardError = $true
    # Only fixed authentication commands reach this helper. No secret is an argument.
    if ($IsWindows -and $resolved.Source -match '\.(cmd|bat)$') {
        foreach ($arg in @($resolved.Source) + $Arguments) {
            if ($arg -match '["\r\n%&|<>^!]') { throw 'LDC:unsafe-cli-argument' }
        }
        $start.FileName = "$env:SystemRoot\System32\cmd.exe"
        $quoted = (@($resolved.Source) + $Arguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
        $start.Arguments = '/d /s /c "' + $quoted + '"'
    } else {
        $start.FileName = $resolved.Source
        foreach ($arg in $Arguments) { $start.ArgumentList.Add($arg) }
    }
    foreach ($name in @('LD_ACCESS_TOKEN','LD_KNOWN_SDK_SECRET')) { [void]$start.Environment.Remove($name) }
    $start.Environment['AZURE_CORE_COLLECT_TELEMETRY'] = 'false'
    $start.Environment['AZURE_CORE_LOG_LEVEL'] = 'error'
    $start.Environment['CLOUDSDK_CORE_LOG_HTTP'] = 'false'
    $start.Environment['CLOUDSDK_CORE_DISABLE_FILE_LOGGING'] = 'true'
    $start.Environment['CLOUDSDK_CORE_DISABLE_PROMPTS'] = 'true'
    [void]$start.Environment.Remove('GH_DEBUG')
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $start; $started=$false
    try {
        [void]$process.Start()
        $started=$true
        $clock=[Diagnostics.Stopwatch]::StartNew()
        $readers=foreach ($reader in @($process.StandardOutput,$process.StandardError)) {
            $buffer=[char[]]::new(4096)
            @{ Reader=$reader; Buffer=$buffer; Text=[Text.StringBuilder]::new(); End=$false; Task=$reader.ReadAsync($buffer,0,$buffer.Length) }
        }
        do {
            if ($clock.Elapsed.TotalSeconds -gt $script:Ldc.TimeoutSeconds) { throw 'LDC:cli-timeout' }
            foreach ($reader in $readers) {
                if (-not $reader.End -and $reader.Task.IsCompleted) {
                    $count=$reader.Task.GetAwaiter().GetResult()
                    if ($count -eq 0) { $reader.End=$true; continue }
                    if ($reader.Text.Length+$count -gt 65536) { throw 'LDC:cli-output-limit' }
                    [void]$reader.Text.Append($reader.Buffer,0,$count)
                    $reader.Task=$reader.Reader.ReadAsync($reader.Buffer,0,$reader.Buffer.Length)
                }
            }
            if (-not $process.HasExited) { [void]$process.WaitForExit(10) }
            elseif (@($readers | Where-Object { -not $_.End }).Count) { [Threading.Thread]::Sleep(1) }
        } while (-not $process.HasExited -or @($readers | Where-Object { -not $_.End }).Count)
        $value=$readers[0].Text.ToString()
        if ($process.ExitCode -ne 0) { throw "LDC:$Command-authentication-failed" }
        return $value.Trim()
    } catch {
        $code = Get-LdcFailureCode $_
        if ($code -eq 'collection-failed') { $code = 'cli-failed' }
        throw "LDC:$code"
    } finally {
        if ($started -and -not $process.HasExited) { $process.Kill($true); [void]$process.WaitForExit(5000) }
        $process.Dispose()
    }
}

function Get-LdcToken {
    param([string]$Platform, [string]$Subscription)
    if ($Platform -eq 'LaunchDarkly') {
        if (-not $script:Ldc.ManagementToken) { throw 'LDC:missing-management-token' }
        return $script:Ldc.ManagementToken
    }
    $cacheKey = "$Platform/$Subscription"
    if ($script:Ldc.Tokens.ContainsKey($cacheKey)) { return $script:Ldc.Tokens[$cacheKey] }
    switch ($Platform) {
        'GitHub' { $token = Invoke-LdcNative gh @('auth','token','--hostname','github.com') }
        'GCP' { $token = Invoke-LdcNative gcloud @('auth','print-access-token','--quiet','--verbosity=error') }
        default {
            $resource = switch ($Platform) {
                'Azure' { 'https://management.azure.com/' }
                'KeyVault' { 'https://vault.azure.net' }
                'AzureDevOps' { '499b84ac-1321-427f-aa17-267ca6975798' }
                default { throw 'LDC:unsupported-platform' }
            }
            $arguments = @('account','get-access-token','--resource',$resource,'--query','accessToken','--output','tsv','--only-show-errors')
            if ($Subscription) { $arguments += @('--subscription',$Subscription) }
            $token = Invoke-LdcNative az $arguments
        }
    }
    if (-not $token -or $token -match '\s') { throw 'LDC:invalid-cli-token' }
    Register-LdcSensitive $token
    $script:Ldc.Tokens[$cacheKey] = $token
    return $token
}

function Assert-LdcRequest {
    param([string]$Platform, [uri]$Uri, [string]$Method)
    if ($Uri.Scheme -ne 'https' -or $Uri.Port -ne 443 -or $Uri.UserInfo -or $Uri.Fragment -or $Uri.OriginalString -match '[\\\r\n]') { throw 'LDC:unsafe-request' }
    $path = [uri]::UnescapeDataString($Uri.AbsolutePath)
    if ($path -match '(^|/)\.\.?(/|$)') { throw 'LDC:unsafe-request' }
    $ok = $false
    switch ($Platform) {
        'LaunchDarkly' {
            $ok = $Uri.Host -eq 'app.launchdarkly.com' -and $path -in @('/api/v2/applications','/api/v2/usage/service-connections') -and $Method -eq 'GET'
        }
        'Azure' {
            foreach ($id in @($script:Ldc.Config.azure.subscriptionIds)) {
                if ($Uri.Host -eq 'management.azure.com' -and $path.StartsWith("/subscriptions/$id/", [StringComparison]::OrdinalIgnoreCase) -and $Method -eq 'GET') { $ok = $true }
            }
            if ($Uri.Host -eq 'management.azure.com' -and $path -eq '/providers/Microsoft.ResourceGraph/resources' -and $Method -eq 'POST') { $ok = $true }
        }
        'KeyVault' {
            $ok = $script:Ldc.AllowedVaultHosts.Contains($Uri.Host) -and $path -match '^/secrets(?:/|$)' -and $Method -eq 'GET'
        }
        'GCP' {
            $projectIds=@($script:Ldc.Config.gcp.projectIds) + @($script:Ldc.GcpProjectAliases.Values)
            foreach ($id in $projectIds) {
                if ($Uri.Host -in @('cloudasset.googleapis.com','secretmanager.googleapis.com') -and
                    ($path.StartsWith("/v1/projects/$id/", [StringComparison]::Ordinal) -or $path -eq "/v1/projects/${id}:searchAllResources") -and $Method -eq 'GET') { $ok = $true }
            }
            foreach ($id in @($script:Ldc.Config.gcp.projectIds)) {
                if ($Uri.Host -eq 'cloudresourcemanager.googleapis.com' -and $path -ceq "/v3/projects/$id" -and $Method -eq 'GET') { $ok=$true }
            }
        }
        'GitHub' {
            if ($Uri.Host -eq 'api.github.com' -and $Method -eq 'GET') {
                foreach ($org in @($script:Ldc.Config.github.organizations)) {
                    if ($path.StartsWith("/orgs/$org/", [StringComparison]::OrdinalIgnoreCase) -or $path.StartsWith("/repos/$org/", [StringComparison]::OrdinalIgnoreCase)) { $ok = $true }
                }
                foreach ($repo in @($script:Ldc.Config.github.repositories)) {
                    if ($path -eq "/repos/$repo" -or $path.StartsWith("/repos/$repo/", [StringComparison]::OrdinalIgnoreCase)) { $ok = $true }
                }
            }
        }
        'AzureDevOps' {
            if ($Uri.Host -in @('dev.azure.com','vsrm.dev.azure.com') -and $Method -eq 'GET') {
                foreach ($org in @($script:Ldc.Config.azureDevOps)) {
                    foreach ($project in @($org.projects)) {
                        if ($path.StartsWith("/$($org.organization)/$project/", [StringComparison]::OrdinalIgnoreCase)) { $ok = $true }
                    }
                }
            }
        }
    }
    if (-not $ok) { throw 'LDC:request-outside-scope' }
}

function Invoke-LdcHttpAttempt {
    param([string]$Platform, [uri]$Uri, [string]$Method, [string]$Token, [string]$Body)
    $handler = [Net.Http.HttpClientHandler]::new(); $handler.AllowAutoRedirect = $false; $handler.UseCookies = $false
    $client = [Net.Http.HttpClient]::new($handler); $client.Timeout = [timespan]::FromSeconds($script:Ldc.TimeoutSeconds)
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::new($Method), $Uri)
    $response = $null; $stream = $null; $buffered = [IO.MemoryStream]::new()
    try {
        $auth = if ($Platform -eq 'LaunchDarkly') { $Token } else { "Bearer $Token" }
        [void]$request.Headers.TryAddWithoutValidation('Authorization', $auth)
        [void]$request.Headers.TryAddWithoutValidation('User-Agent', 'LaunchDarklyConsumerInventory/1')
        if ($Platform -eq 'LaunchDarkly') { [void]$request.Headers.TryAddWithoutValidation('LD-API-Version','beta') }
        if ($Platform -eq 'GitHub') {
            [void]$request.Headers.TryAddWithoutValidation('Accept','application/vnd.github+json')
            [void]$request.Headers.TryAddWithoutValidation('X-GitHub-Api-Version','2022-11-28')
        }
        if ($Body) { $request.Content = [Net.Http.StringContent]::new($Body, [Text.Encoding]::UTF8, 'application/json') }
        $response = $client.SendAsync($request, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $headers = @{}
        foreach ($header in $response.Headers) { $headers[$header.Key] = $header.Value -join ',' }
        $status = [int]$response.StatusCode
        # Error bodies may contain secrets: never read or return them.
        if ($status -ge 200 -and $status -lt 300) {
            $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
            $bytes = [byte[]]::new(8192)
            $cancel = [Threading.CancellationTokenSource]::new([timespan]::FromSeconds($script:Ldc.TimeoutSeconds))
            try {
                while (($read = $stream.ReadAsync($bytes, 0, $bytes.Length, $cancel.Token).GetAwaiter().GetResult()) -gt 0) {
                    if ($buffered.Length + $read -gt 20MB) { throw 'LDC:response-size-limit' }
                    $buffered.Write($bytes,0,$read)
                }
            } finally { $cancel.Dispose() }
        }
        return @{ Status=$status; Headers=$headers; Text=[Text.Encoding]::UTF8.GetString($buffered.ToArray()) }
    } catch {
        $code = Get-LdcFailureCode $_
        if ($code -eq 'collection-failed') { $code = 'request-failed' }
        throw "LDC:$code"
    } finally {
        if ($stream) { $stream.Dispose() }; $buffered.Dispose()
        if ($response) { $response.Dispose() }; $request.Dispose(); $client.Dispose()
    }
}

function Invoke-LdcRequest {
    param([string]$Platform, [string]$Uri, [ValidateSet('GET','POST')][string]$Method = 'GET', [string]$Body, [string]$Subscription, [switch]$Raw)
    Assert-LdcRequest $Platform ([uri]$Uri) $Method
    if ($Method -eq 'POST') {
        try { $query = ConvertFrom-Json -AsHashtable -InputObject $Body -ErrorAction Stop } catch { throw 'LDC:invalid-read-query' }
        if (-not $query.subscriptions -or @($query.subscriptions | Where-Object { $_ -notin $script:Ldc.Config.azure.subscriptionIds }).Count) { throw 'LDC:request-outside-scope' }
    }
    $token = Get-LdcToken $Platform $Subscription
    for ($attempt=0; $attempt -le $script:Ldc.MaxRetries; $attempt++) {
        $result = Invoke-LdcHttpAttempt $Platform ([uri]$Uri) $Method $token $Body
        if ($result.Status -ge 200 -and $result.Status -lt 300) {
            if ($Raw) { return @{ Data=$result.Text; Headers=$result.Headers } }
            try { $data = ConvertFrom-Json -AsHashtable -NoEnumerate -InputObject $result.Text -Depth 100 -ErrorAction Stop } catch { throw 'LDC:invalid-json' }
            return @{ Data=$data; Headers=$result.Headers }
        }
        if ($result.Status -in @(429,500,502,503,504) -and $attempt -lt $script:Ldc.MaxRetries) {
            $delay = [math]::Pow(2,$attempt)
            if ($result.Headers['Retry-After']) {
                $seconds = 0.0; $date = [datetimeoffset]::MinValue
                if ([double]::TryParse($result.Headers['Retry-After'], [ref]$seconds)) { $delay = [math]::Max(0,$seconds) }
                elseif ([datetimeoffset]::TryParse($result.Headers['Retry-After'],[ref]$date)) { $delay=[math]::Max(0,($date-[datetimeoffset]::UtcNow).TotalSeconds) }
                else { throw 'LDC:invalid-retry-after' }
            }
            if ($delay -gt 60) { throw 'LDC:retry-window-exceeded' }
            Start-Sleep -Milliseconds ([int][math]::Ceiling($delay*1000)); continue
        }
        throw "LDC:http-$($result.Status)"
    }
}

function Get-LdcCanonicalReference {
    param([string]$Reference)
    $reference=$Reference.TrimEnd('/')
    if ($reference -cmatch '^projects/([^/]+)/secrets/([^/]+)$') {
        $project=$Matches[1]; $name=$Matches[2]
        if ($script:Ldc.GcpProjectAliases.ContainsKey($project)) {
            return "projects/$($script:Ldc.GcpProjectAliases[$project])/secrets/$name"
        }
    }
    return $reference
}

function Resolve-LdcReferences {
    $matchesByReference = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    foreach ($row in $script:Ldc.Findings) {
        if ($row.Classification -eq 'ExactStoredKeyMatch' -and $row.SecretReference) {
            $key = Get-LdcCanonicalReference $row.SecretReference
            if (-not $matchesByReference.ContainsKey($key)) { $matchesByReference[$key]=[Collections.Generic.List[object]]::new() }
            $matchesByReference[$key].Add($row)
        }
    }
    foreach ($row in $script:Ldc.Findings) {
        if ($row.Classification -eq 'UnresolvedCandidate' -and $row.SecretReference) {
            $key = Get-LdcCanonicalReference $row.SecretReference
            $possible = @($matchesByReference[$key] | Where-Object { $_ -and $row.SecretVersion -and $_.SecretVersion -ceq $row.SecretVersion })
            if ($possible.Count) {
                $row.Classification = 'ConfigurationReferenceLinkedToMatch'
                $row.NextStep = 'Verify the deployment resolves this configuration and identify its update owner; runtime use is unproven.'
            }
        }
    }
}

function Get-LdcReport {
    Resolve-LdcReferences
    $rows = foreach ($item in $script:Ldc.Findings) {
        $safe = [ordered]@{}
        foreach ($property in $item.PSObject.Properties) { $safe[$property.Name] = Protect-LdcText $property.Value }
        [pscustomobject]$safe
    }
    $scopes = foreach ($item in $script:Ldc.Coverage) {
        @{ Platform=(Protect-LdcText $item.Platform); Scope=(Protect-LdcText $item.Scope); Status=$item.Status; Reason=(Protect-LdcText $item.Reason) }
    }
    return [ordered]@{
        schemaVersion=1; generatedAt=[datetimeoffset]::UtcNow.ToString('o')
        observationWindow=@{from=$script:Ldc.From.ToString('o');to=$script:Ldc.To.ToString('o')}
        status=$(if ($script:Ldc.GapCount) {'incomplete'} else {'accessible-inventory-collected'})
        consumerCompleteness='not-established'; scopes=@($scopes); findings=@($rows)
        scopeSummary=@{
            requested=@($scopes | Where-Object Status -ne 'unsupported').Count
            scanned=@($scopes | Where-Object Status -eq 'scanned').Count
            partial=@($scopes | Where-Object Status -eq 'partial').Count
            failed=@($scopes | Where-Object Status -eq 'failed').Count
            unsupported=@($scopes | Where-Object Status -eq 'unsupported').Count
        }
        limitations=@('Stored credentials and environment activity do not prove runtime consumption.',
            'Workload internals, non-default branches except pipeline references, and dormant or inaccessible configurations require follow-up.')
    }
}

function Write-LdcReport {
    param([string]$OutputDirectory)
    $report = Get-LdcReport
    $directory = [IO.Path]::GetFullPath($OutputDirectory)
    [void][IO.Directory]::CreateDirectory($directory)
    $jsonPath = Join-Path $directory 'launchdarkly-consumers.json'; $csvPath = Join-Path $directory 'launchdarkly-consumers.csv'
    if ([IO.File]::Exists($jsonPath) -or [IO.File]::Exists($csvPath)) { throw 'LDC:output-already-exists' }
    $json = ConvertTo-Json -InputObject $report -Depth 20
    $csvRows = foreach ($row in $report.findings) {
        $safe = [ordered]@{}
        foreach ($p in $row.PSObject.Properties) {
            $v = [string]$p.Value
            if ($v -match '^\s*[=+@-]') { $v = "'" + $v }
            $safe[$p.Name]=$v
        }
        [pscustomobject]$safe
    }
    $csv = if (@($csvRows).Count) { @($csvRows | ConvertTo-Csv -NoTypeInformation) -join [Environment]::NewLine } else { '"Platform","Scope","Resource","Classification","Application","Environment","SecretReference","SecretVersion","Owner","FirstObservedBucket","LastObservedBucket","Evidence","NextStep"' }
    foreach ($file in @(@{Path=$jsonPath;Text=$json},@{Path=$csvPath;Text=$csv})) {
        $handle = [IO.File]::Open($file.Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        try { $bytes=[Text.UTF8Encoding]::new($false).GetBytes($file.Text); $handle.Write($bytes,0,$bytes.Length) } finally { $handle.Dispose() }
    }
    return $report.status
}
