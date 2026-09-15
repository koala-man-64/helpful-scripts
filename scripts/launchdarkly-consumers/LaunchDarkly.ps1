function Invoke-LdcLaunchDarkly {
    $config = $script:Ldc.Config.launchDarkly
    $scope = "$($config.projectKey)/$($config.environmentKey)"
    Invoke-LdcScope -Platform LaunchDarkly -Scope $scope -Action {
        $applications = @{}
        try {
            $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            $offset=0; $complete=$false; $expectedTotal=$null
            for ($page=0; $page -lt $script:Ldc.MaxPages; $page++) {
                $response=Invoke-LdcRequest -Platform LaunchDarkly -Uri "https://app.launchdarkly.com/api/v2/applications?limit=100&offset=$offset"
                $data=$response.Data
                if ($data -isnot [Collections.IDictionary] -or $data.items -isnot [array]) { throw 'LDC:invalid-applications-response' }
                if ($data.Contains('totalCount')) {
                    if ($data.totalCount -isnot [long] -and $data.totalCount -isnot [int]) { throw 'LDC:invalid-applications-total' }
                    if ($data.totalCount -lt 0) { throw 'LDC:invalid-applications-total' }
                    if ($null -ne $expectedTotal -and $data.totalCount -ne $expectedTotal) { throw 'LDC:applications-total-changed' }
                    $expectedTotal=$data.totalCount
                }
                foreach ($app in $data.items) {
                    if (-not $app.key -or -not $seen.Add([string]$app.key)) { throw 'LDC:duplicate-or-missing-application' }
                    $applications[[string]$app.key]=$app
                }
                $offset+=@($data.items).Count
                if ($null -ne $expectedTotal -and $offset -gt $expectedTotal) { throw 'LDC:applications-total-mismatch' }
                if (@($data.items).Count -eq 0) {
                    if ($null -ne $expectedTotal -and $offset -lt $expectedTotal) { throw 'LDC:applications-page-missing' }
                    $complete=$true; break
                }
                if ($null -ne $expectedTotal -and $offset -eq $expectedTotal) { $complete=$true; break }
            }
            if (-not $complete) { throw 'LDC:application-page-limit' }
        } catch { Add-LdcGap -Platform LaunchDarkly -Scope $scope -Resource applications -Reason (Get-LdcFailureCode $_) }
        $project=[uri]::EscapeDataString($config.projectKey); $environment=[uri]::EscapeDataString($config.environmentKey)
        $from=$script:Ldc.From.ToUnixTimeMilliseconds(); $to=$script:Ldc.To.ToUnixTimeMilliseconds()
        $uri="https://app.launchdarkly.com/api/v2/usage/service-connections?projectKey=$project&environmentKey=$environment&from=$from&to=$to&groupBy=sdkAppId&groupBy=sdkName&groupBy=sdkVersion&groupBy=connectionType&aggregationType=incremental&granularity=daily"
        $usage=(Invoke-LdcRequest -Platform LaunchDarkly -Uri $uri).Data
        if ($usage -isnot [Collections.IDictionary] -or $usage.metadata -isnot [array] -or $usage.series -isnot [array]) { throw 'LDC:invalid-usage-response' }
        foreach ($point in $usage.series) {
            if ($point -isnot [Collections.IDictionary] -or -not $point.Contains('time') -or $point.time -isnot [long] -and $point.time -isnot [int]) { throw 'LDC:invalid-usage-time' }
            foreach ($key in $point.Keys) {
                if ($key -eq 'time') { continue }
                if ($key -notmatch '^\d+$' -or [long]$key -ge $usage.metadata.Count) { throw 'LDC:usage-series-metadata-mismatch' }
                if ($point[$key] -isnot [ValueType] -or $point[$key] -is [bool] -or [double]$point[$key] -lt 0) { throw 'LDC:invalid-usage-value' }
            }
        }
        for ($i=0; $i -lt @($usage.metadata).Count; $i++) {
            $metadata=$usage.metadata[$i]; $index=[string]$i
            if ($metadata -isnot [Collections.IDictionary]) { throw 'LDC:invalid-usage-metadata' }
            $buckets=[Collections.Generic.List[long]]::new()
            foreach ($point in $usage.series) {
                if (-not $point.Contains('time')) { throw 'LDC:missing-usage-time' }
                if ($point.Contains($index) -and [double]$point[$index] -gt 0) {
                    $time=[long]$point.time
                    if ($time -ge $from -and $time -le $to) { $buckets.Add($time) }
                }
            }
            if (-not $buckets.Count) { continue }
            $appId=[string]$metadata.sdkAppId
            $appName=$appId
            if ($appId -and $applications.ContainsKey($appId)) { $appName=[string]$applications[$appId].name }
            if (-not $appId) {
                $appId='Unknown'; $appName='Unknown'
                Add-LdcGap -Platform LaunchDarkly -Scope $scope -Resource service-connections -Reason application-id-missing -NextStep 'Identify the workload from SDK/configuration evidence; configure application metadata through its owner.'
            }
            $first=($buckets | Measure-Object -Minimum).Minimum; $last=($buckets | Measure-Object -Maximum).Maximum
            Add-LdcFinding -Platform LaunchDarkly -Scope $scope -Resource $appId -Application $appName -Environment $config.environmentKey `
                -Classification EnvironmentActivity -FirstObservedBucket ([datetimeoffset]::FromUnixTimeMilliseconds([long]$first).ToString('o')) `
                -LastObservedBucket ([datetimeoffset]::FromUnixTimeMilliseconds([long]$last).ToString('o')) `
                -Evidence ("sdk={0}; version={1}; connectionType={2}; daily activity buckets; credential attribution unavailable" -f $metadata.sdkName,$metadata.sdkVersion,$metadata.connectionType) `
                -NextStep 'Trace deployed configuration to the exact credential; this is environment activity only.'
        }
        if (@($usage.series).Count -and -not @($usage.metadata).Count) { Add-LdcGap -Platform LaunchDarkly -Scope $scope -Resource service-connections -Reason usage-metadata-missing }
    }
}
