[CmdletBinding()]
param(
    [switch]$UseDeviceCode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-AzCli {
    param(
        [Parameter(Mandatory)]
        [string[]]$Arguments,

        [switch]$ExpectJson
    )

    $allArguments = [System.Collections.Generic.List[string]]::new()
    $allArguments.AddRange($Arguments)
    $allArguments.Add("--only-show-errors")

    if ($ExpectJson) {
        $allArguments.Add("--output")
        $allArguments.Add("json")
    }
    else {
        $allArguments.Add("--output")
        $allArguments.Add("none")
    }

    $output = & az @allArguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $message = ($output | Out-String).Trim()
        throw "Azure CLI command failed: az $($Arguments -join ' ')`n$message"
    }

    if (-not $ExpectJson) {
        return
    }

    $json = ($output | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($json)) {
        return @()
    }

    $result = $json | ConvertFrom-Json
    if ($result -is [System.Array]) {
        return $result
    }

    return @($result)
}

function Select-Item {
    param(
        [Parameter(Mandatory)]
        [string]$Prompt,

        [Parameter(Mandatory)]
        [object[]]$Items,

        [Parameter(Mandatory)]
        [scriptblock]$DisplayScript
    )

    if ($Items.Count -eq 0) {
        throw "No items are available for selection."
    }

    for ($index = 0; $index -lt $Items.Count; $index++) {
        $label = & $DisplayScript $Items[$index]
        Write-Host ("[{0}] {1}" -f ($index + 1), $label)
    }

    while ($true) {
        $selection = Read-Host $Prompt
        $parsedSelection = 0

        if ([int]::TryParse($selection, [ref]$parsedSelection) -and $parsedSelection -ge 1 -and $parsedSelection -le $Items.Count) {
            return $Items[$parsedSelection - 1]
        }

        Write-Warning "Enter a number between 1 and $($Items.Count)."
    }
}

function Ensure-AzCli {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw "Azure CLI is not installed or is not on PATH."
    }
}

function Ensure-AzLogin {
    try {
        $null = Invoke-AzCli -Arguments @("account", "show") -ExpectJson
    }
    catch {
        Write-Host "Signing in to Azure..."

        $loginArguments = [System.Collections.Generic.List[string]]::new()
        $loginArguments.Add("login")

        if ($UseDeviceCode) {
            $loginArguments.Add("--use-device-code")
        }

        Invoke-AzCli -Arguments $loginArguments.ToArray()
    }
}

function Select-Subscription {
    $subscriptions = @(Invoke-AzCli -Arguments @("account", "list") -ExpectJson | Sort-Object -Property name)
    if ($subscriptions.Count -eq 0) {
        throw "No Azure subscriptions are available for the signed-in account."
    }

    $currentSubscription = (Invoke-AzCli -Arguments @("account", "show") -ExpectJson)[0]

    if ($subscriptions.Count -eq 1) {
        Write-Host "Using subscription $($subscriptions[0].name) ($($subscriptions[0].id))."
        return $subscriptions[0]
    }

    Write-Host "Select an Azure subscription:"
    $selectedSubscription = Select-Item `
        -Prompt "Choose a subscription number" `
        -Items $subscriptions `
        -DisplayScript {
            param($subscription)

            $defaultMarker = if ($subscription.id -eq $currentSubscription.id) { " [current]" } else { "" }
            "{0} ({1}){2}" -f $subscription.name, $subscription.id, $defaultMarker
        }

    Invoke-AzCli -Arguments @("account", "set", "--subscription", $selectedSubscription.id)
    Write-Host "Using subscription $($selectedSubscription.name) ($($selectedSubscription.id))."

    return $selectedSubscription
}

function Get-AdlsAccounts {
    param(
        [Parameter(Mandatory)]
        [string]$ResourceGroupName
    )

    $storageAccounts = @(Invoke-AzCli -Arguments @("storage", "account", "list", "--resource-group", $ResourceGroupName) -ExpectJson)

    return @(
        $storageAccounts |
            Where-Object { $_.isHnsEnabled -eq $true } |
            Sort-Object -Property name
    )
}

function Test-StorageFsCommandUnavailable {
    param(
        [Parameter(Mandatory)]
        [string]$Message
    )

    return $Message -match "invalid choice: 'fs'" `
        -or $Message -match "The command requires the extension" `
        -or $Message -match "No module named 'azure\.cli\.command_modules\.storage'"
}

function Get-Containers {
    param(
        [Parameter(Mandatory)]
        [string]$StorageAccountName
    )

    try {
        return @(Invoke-AzCli -Arguments @("storage", "fs", "list", "--account-name", $StorageAccountName, "--auth-mode", "login") -ExpectJson | Sort-Object -Property name)
    }
    catch {
        if (Test-StorageFsCommandUnavailable -Message $_.Exception.Message) {
            throw "Azure CLI storage filesystem commands are unavailable. Install or upgrade the storage extension, for example: az extension add --name storage-preview --upgrade"
        }

        if ($_.Exception.Message -match "AuthorizationPermissionMismatch|AuthorizationFailure|This request is not authorized") {
            throw "The signed-in identity does not have enough data-plane access to list containers in '$StorageAccountName'. Grant a role such as Storage Blob Data Reader or Storage Blob Data Contributor and retry."
        }

        throw
    }
}

function Get-DirectoryEntries {
    param(
        [Parameter(Mandatory)]
        [string]$StorageAccountName,

        [Parameter(Mandatory)]
        [string]$ContainerName,

        [string]$Path
    )

    $arguments = [System.Collections.Generic.List[string]]::new()
    $arguments.AddRange(@(
        "storage",
        "fs",
        "file",
        "list",
        "--account-name",
        $StorageAccountName,
        "--file-system",
        $ContainerName,
        "--auth-mode",
        "login"
    ))

    if (-not [string]::IsNullOrWhiteSpace($Path)) {
        $arguments.Add("--path")
        $arguments.Add($Path)
    }

    try {
        return @(Invoke-AzCli -Arguments $arguments.ToArray() -ExpectJson)
    }
    catch {
        if (Test-StorageFsCommandUnavailable -Message $_.Exception.Message) {
            throw "Azure CLI storage filesystem commands are unavailable. Install or upgrade the storage extension, for example: az extension add --name storage-preview --upgrade"
        }

        if ($_.Exception.Message -match "AuthorizationPermissionMismatch|AuthorizationFailure|This request is not authorized") {
            throw "The signed-in identity does not have enough data-plane access to list files in '$ContainerName'. Grant a role such as Storage Blob Data Reader or Storage Blob Data Contributor and retry."
        }

        throw
    }
}

function Show-DirectoryTree {
    param(
        [Parameter(Mandatory)]
        [string]$StorageAccountName,

        [Parameter(Mandatory)]
        [string]$ContainerName,

        [string]$Path = "",

        [int]$Depth = 0
    )

    $indent = "  " * $Depth
    $directoryLabel = if ([string]::IsNullOrWhiteSpace($Path)) { "[root]" } else { ($Path -split "/")[-1] }

    Write-Host ("{0}{1}/" -f $indent, $directoryLabel)

    $entries = @(Get-DirectoryEntries -StorageAccountName $StorageAccountName -ContainerName $ContainerName -Path $Path)
    $directories = @($entries | Where-Object { $_.isDirectory } | Sort-Object -Property name)
    $files = @($entries | Where-Object { -not $_.isDirectory } | Sort-Object -Property name)

    foreach ($file in $files) {
        $fileName = ($file.name -split "/")[-1]
        Write-Host ("{0}  {1}" -f $indent, $fileName)
    }

    foreach ($directory in $directories) {
        Show-DirectoryTree `
            -StorageAccountName $StorageAccountName `
            -ContainerName $ContainerName `
            -Path $directory.name `
            -Depth ($Depth + 1)
    }
}

try {
    Ensure-AzCli
    Ensure-AzLogin
    $null = Select-Subscription

    $resourceGroups = @(Invoke-AzCli -Arguments @("group", "list") -ExpectJson | Sort-Object -Property name)
    if ($resourceGroups.Count -eq 0) {
        throw "No resource groups are available in the selected subscription."
    }

    Write-Host "Select a resource group:"
    $selectedResourceGroup = Select-Item `
        -Prompt "Choose a resource group number" `
        -Items $resourceGroups `
        -DisplayScript { param($group) "{0} ({1})" -f $group.name, $group.location }

    $adlsAccounts = @(Get-AdlsAccounts -ResourceGroupName $selectedResourceGroup.name)
    if ($adlsAccounts.Count -eq 0) {
        throw "No ADLS Gen2 accounts were found in resource group '$($selectedResourceGroup.name)'."
    }

    Write-Host ""
    Write-Host "Select an ADLS account:"
    $selectedAccount = Select-Item `
        -Prompt "Choose a storage account number" `
        -Items $adlsAccounts `
        -DisplayScript { param($account) "{0} ({1}, {2})" -f $account.name, $account.primaryLocation, $account.sku.name }

    $containers = @(Get-Containers -StorageAccountName $selectedAccount.name)
    if ($containers.Count -eq 0) {
        throw "No containers were found in storage account '$($selectedAccount.name)'."
    }

    Write-Host ""
    Write-Host "Select a container:"
    $selectedContainer = Select-Item `
        -Prompt "Choose a container number" `
        -Items $containers `
        -DisplayScript { param($container) $container.name }

    Write-Host ""
    Write-Host ("Listing contents of {0}/{1}" -f $selectedAccount.name, $selectedContainer.name)
    Show-DirectoryTree -StorageAccountName $selectedAccount.name -ContainerName $selectedContainer.name
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
