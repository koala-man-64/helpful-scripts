# GitTools.ps1
# Single-file Git command dispatcher for common Git operations plus draft-only PR support.
# Usage examples:
#   .\GitTools.ps1 status -sb
#   .\GitTools.ps1 add .
#   .\GitTools.ps1 commit -m "Fix bug"
#   .\GitTools.ps1 graph
#   .\GitTools.ps1 pr draft --base main --title "My change" --body "Summary"
#   .\GitTools.ps1 pr list
#
# Dot-source the file to load the functions without running a command:
#   . .\GitTools.ps1

param(
    [Parameter(Position = 0)]
    [AllowNull()]
    [string]$Command,

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [AllowNull()]
    [string[]]$Remaining
)

Set-StrictMode -Version Latest

$script:GitToolExitCode = 0

function Show-GitToolHelp {
@'
GitTools.ps1

Syntax:
  .\GitTools.ps1 <command> [args...]
  .\GitTools.ps1 pr draft [gh-pr-create-args...]
  .\GitTools.ps1 pr <list|view|status|checks|diff|checkout> [args...]
  .\GitTools.ps1 git <allowed-git-subcommand> [args...]

Common commands:
  status, clone, init, add, rm, mv, restore, commit, amend
  branch, switch, checkout, merge, rebase, fetch, pull, push
  log, graph, diff, show, stash, tag, remote, reset, clean
  cherry-pick, revert, bisect, blame, grep, config, worktree
  submodule, lfs, reflog, describe, archive, apply, am, format-patch
  gc, fsck, shortlog, ls-files

Convenience commands:
  current-branch             -> git branch --show-current
  root                       -> git rev-parse --show-toplevel
  last                       -> git log -1 --stat
  unstage <paths...>         -> git restore --staged <paths...>
  unstage --all              -> git restore --staged .
  discard <paths...>         -> git restore <paths...>
  discard --all              -> git restore .

Draft-only PR commands:
  pr draft [args...]         -> gh pr create [args...] --draft
  pr make-draft [pr]         -> gh pr ready [pr] --undo
  draft-pr [args...]         -> alias for pr draft

Blocked PR actions:
  Non-draft PR creation, ready-for-review, merge, review, close, reopen,
  edit, comment, lock/unlock, update-branch, and browser-based PR creation.

Notes:
  - No command runs unless you pass a command.
  - GitHub PR drafting requires the GitHub CLI: gh.
  - pr draft adds --head <current-branch> when --head/-H is omitted so gh will not
    prompt to push/fork implicitly. Push explicitly with the push command first.
  - Set GITTOOLS_ECHO=1 to print the external command before it runs.
'@ | Write-Output
}

function Show-GitToolPrHelp {
@'
PR usage:
  .\GitTools.ps1 pr draft --base main --title "Title" --body "Body"
  .\GitTools.ps1 pr draft --base main --fill
  .\GitTools.ps1 pr make-draft 123
  .\GitTools.ps1 pr list
  .\GitTools.ps1 pr view 123

This script only allows draft creation/conversion and read/checkout PR operations.
It intentionally blocks ready, merge, review, close, edit, comment, and non-draft create.
'@ | Write-Output
}

function Assert-Executable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required executable '$Name' was not found on PATH."
    }
}

function New-ArgList {
    param(
        [AllowNull()]
        [string[]]$Prefix,

        [AllowNull()]
        [string[]]$Rest
    )

    $items = New-Object System.Collections.Generic.List[string]

    if ($null -ne $Prefix) {
        foreach ($item in $Prefix) {
            if ($null -ne $item) { [void]$items.Add($item) }
        }
    }

    if ($null -ne $Rest) {
        foreach ($item in $Rest) {
            if ($null -ne $item) { [void]$items.Add($item) }
        }
    }

    return $items.ToArray()
}

function Get-TailArgs {
    param(
        [AllowNull()]
        [string[]]$Items
    )

    if ($null -eq $Items -or $Items.Count -le 1) {
        return @()
    }

    return @($Items[1..($Items.Count - 1)])
}

function Invoke-ExternalCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FileName,

        [AllowNull()]
        [string[]]$ArgumentList
    )

    $argsToRun = @()
    if ($null -ne $ArgumentList) { $argsToRun = @($ArgumentList) }

    if ($env:GITTOOLS_ECHO -eq '1') {
        Write-Host "+ $FileName $($argsToRun -join ' ')"
    }

    & $FileName @argsToRun

    if ($null -eq $global:LASTEXITCODE) {
        $script:GitToolExitCode = 0
    }
    else {
        $script:GitToolExitCode = [int]$global:LASTEXITCODE
    }
}

function Invoke-Git {
    param(
        [AllowNull()]
        [string[]]$GitArguments
    )

    Assert-Executable -Name 'git'
    Invoke-ExternalCommand -FileName 'git' -ArgumentList $GitArguments
}

function Invoke-Gh {
    param(
        [AllowNull()]
        [string[]]$GhArguments
    )

    Assert-Executable -Name 'gh'
    Invoke-ExternalCommand -FileName 'gh' -ArgumentList $GhArguments
}

function Invoke-GitSubcommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Subcommand,

        [AllowNull()]
        [string[]]$ArgsForSubcommand
    )

    Invoke-Git -GitArguments (New-ArgList -Prefix @($Subcommand) -Rest $ArgsForSubcommand)
}

function Invoke-GitAmend {
    param([AllowNull()][string[]]$ArgsForCommand)
    Invoke-Git -GitArguments (New-ArgList -Prefix @('commit', '--amend') -Rest $ArgsForCommand)
}

function Invoke-GitGraph {
    param([AllowNull()][string[]]$ArgsForCommand)
    Invoke-Git -GitArguments (New-ArgList -Prefix @('log', '--graph', '--decorate', '--oneline', '--all') -Rest $ArgsForCommand)
}

function Invoke-GitCurrentBranch {
    param([AllowNull()][string[]]$ArgsForCommand)
    Invoke-Git -GitArguments (New-ArgList -Prefix @('branch', '--show-current') -Rest $ArgsForCommand)
}

function Invoke-GitRoot {
    param([AllowNull()][string[]]$ArgsForCommand)
    Invoke-Git -GitArguments (New-ArgList -Prefix @('rev-parse', '--show-toplevel') -Rest $ArgsForCommand)
}

function Invoke-GitLast {
    param([AllowNull()][string[]]$ArgsForCommand)
    Invoke-Git -GitArguments (New-ArgList -Prefix @('log', '-1', '--stat') -Rest $ArgsForCommand)
}

function Invoke-GitUnstage {
    param([AllowNull()][string[]]$ArgsForCommand)

    if ($null -eq $ArgsForCommand -or $ArgsForCommand.Count -eq 0) {
        throw "Provide path(s), or use: .\GitTools.ps1 unstage --all"
    }

    if ($ArgsForCommand.Count -eq 1 -and $ArgsForCommand[0] -eq '--all') {
        Invoke-Git -GitArguments @('restore', '--staged', '.')
        return
    }

    Invoke-Git -GitArguments (New-ArgList -Prefix @('restore', '--staged') -Rest $ArgsForCommand)
}

function Invoke-GitDiscard {
    param([AllowNull()][string[]]$ArgsForCommand)

    if ($null -eq $ArgsForCommand -or $ArgsForCommand.Count -eq 0) {
        throw "Provide path(s), or use: .\GitTools.ps1 discard --all"
    }

    if ($ArgsForCommand.Count -eq 1 -and $ArgsForCommand[0] -eq '--all') {
        Invoke-Git -GitArguments @('restore', '.')
        return
    }

    Invoke-Git -GitArguments (New-ArgList -Prefix @('restore') -Rest $ArgsForCommand)
}

function Get-GitOutputTrimmed {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$GitArguments
    )

    Assert-Executable -Name 'git'
    $output = & git @GitArguments 2>$null
    $exitCode = if ($null -eq $global:LASTEXITCODE) { 0 } else { [int]$global:LASTEXITCODE }

    if ($exitCode -ne 0) {
        return $null
    }

    return (($output | Out-String).Trim())
}

function Test-ArgumentHasOption {
    param(
        [AllowNull()]
        [string[]]$ArgsToCheck,

        [Parameter(Mandatory = $true)]
        [string[]]$OptionNames
    )

    if ($null -eq $ArgsToCheck) { return $false }

    foreach ($arg in $ArgsToCheck) {
        foreach ($option in $OptionNames) {
            if ($arg -eq $option -or $arg.StartsWith("$option=")) {
                return $true
            }
        }
    }

    return $false
}

function Remove-AndValidatePrDraftArgs {
    param(
        [AllowNull()]
        [string[]]$ArgsForCommand
    )

    $clean = New-Object System.Collections.Generic.List[string]
    $blockedOptions = @('--web', '-w')

    if ($null -eq $ArgsForCommand) {
        return $clean.ToArray()
    }

    foreach ($arg in $ArgsForCommand) {
        if ($arg -match '^(--draft|-d)(=(?<value>.*))?$') {
            $value = $Matches['value']
            if ($null -ne $value -and $value.Trim().ToLowerInvariant() -in @('false', '0', 'no', 'off')) {
                throw "PR creation is draft-only; '$arg' is not allowed."
            }
            continue
        }

        if ($blockedOptions -contains $arg -or $arg.StartsWith('--web=')) {
            throw "Browser-based PR creation is blocked because it can bypass draft-only enforcement."
        }

        [void]$clean.Add($arg)
    }

    return $clean.ToArray()
}

function Invoke-PullRequestDraft {
    param([AllowNull()][string[]]$ArgsForCommand)

    $cleanArgs = Remove-AndValidatePrDraftArgs -ArgsForCommand $ArgsForCommand

    if (-not (Test-ArgumentHasOption -ArgsToCheck $cleanArgs -OptionNames @('--head', '-H'))) {
        $currentBranch = Get-GitOutputTrimmed -GitArguments @('branch', '--show-current')
        if ([string]::IsNullOrWhiteSpace($currentBranch)) {
            throw "Cannot infer the current branch. Provide --head explicitly, or leave detached HEAD state."
        }
        $cleanArgs = New-ArgList -Prefix $cleanArgs -Rest @('--head', $currentBranch)
    }

    $ghArgs = New-ArgList -Prefix @('pr', 'create') -Rest $cleanArgs
    $ghArgs = New-ArgList -Prefix $ghArgs -Rest @('--draft')
    Invoke-Gh -GhArguments $ghArgs
}

function Invoke-PullRequestMakeDraft {
    param([AllowNull()][string[]]$ArgsForCommand)

    # GitHub CLI uses: gh pr ready [<number>|<url>|<branch>] --undo
    $ghArgs = New-ArgList -Prefix @('pr', 'ready') -Rest $ArgsForCommand
    $ghArgs = New-ArgList -Prefix $ghArgs -Rest @('--undo')
    Invoke-Gh -GhArguments $ghArgs
}

function Invoke-PullRequestReadOrCheckout {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PrSubcommand,

        [AllowNull()]
        [string[]]$ArgsForCommand
    )

    Invoke-Gh -GhArguments (New-ArgList -Prefix @('pr', $PrSubcommand) -Rest $ArgsForCommand)
}

function Invoke-PullRequestCommand {
    param([AllowNull()][string[]]$ArgsForCommand)

    if ($null -eq $ArgsForCommand -or $ArgsForCommand.Count -eq 0) {
        Show-GitToolPrHelp
        return
    }

    $subcommand = $ArgsForCommand[0].Trim().ToLowerInvariant()
    $tail = Get-TailArgs -Items $ArgsForCommand

    switch ($subcommand) {
        'draft'      { Invoke-PullRequestDraft -ArgsForCommand $tail; return }
        'new'        { Invoke-PullRequestDraft -ArgsForCommand $tail; return }
        'make-draft' { Invoke-PullRequestMakeDraft -ArgsForCommand $tail; return }
        'to-draft'   { Invoke-PullRequestMakeDraft -ArgsForCommand $tail; return }
        'list'       { Invoke-PullRequestReadOrCheckout -PrSubcommand 'list' -ArgsForCommand $tail; return }
        'ls'         { Invoke-PullRequestReadOrCheckout -PrSubcommand 'list' -ArgsForCommand $tail; return }
        'view'       { Invoke-PullRequestReadOrCheckout -PrSubcommand 'view' -ArgsForCommand $tail; return }
        'status'     { Invoke-PullRequestReadOrCheckout -PrSubcommand 'status' -ArgsForCommand $tail; return }
        'checks'     { Invoke-PullRequestReadOrCheckout -PrSubcommand 'checks' -ArgsForCommand $tail; return }
        'diff'       { Invoke-PullRequestReadOrCheckout -PrSubcommand 'diff' -ArgsForCommand $tail; return }
        'checkout'   { Invoke-PullRequestReadOrCheckout -PrSubcommand 'checkout' -ArgsForCommand $tail; return }
        default {
            throw "PR subcommand '$subcommand' is blocked. Allowed: draft, make-draft, list, view, status, checks, diff, checkout."
        }
    }
}

function Get-GitSubcommandFromArgs {
    param([AllowNull()][string[]]$GitArguments)

    if ($null -eq $GitArguments -or $GitArguments.Count -eq 0) {
        return $null
    }

    $i = 0
    while ($i -lt $GitArguments.Count) {
        $arg = $GitArguments[$i]

        if ($arg -in @('-C', '-c', '--git-dir', '--work-tree', '--namespace', '--exec-path', '--super-prefix', '--config-env')) {
            $i += 2
            continue
        }

        if ($arg -match '^--(git-dir|work-tree|namespace|exec-path|super-prefix|config-env)=') {
            $i++
            continue
        }

        if ($arg -match '^--(bare|no-pager|paginate|literal-pathspecs|glob-pathspecs|noglob-pathspecs|icase-pathspecs|no-replace-objects|no-optional-locks)$') {
            $i++
            continue
        }

        if ($arg -in @('--version', '--help')) {
            return $arg.TrimStart('-').ToLowerInvariant()
        }

        if ($arg.StartsWith('-')) {
            $i++
            continue
        }

        return $arg.Trim().ToLowerInvariant()
    }

    return $null
}

function Invoke-GitPassThrough {
    param([AllowNull()][string[]]$ArgsForCommand)

    if ($null -eq $ArgsForCommand -or $ArgsForCommand.Count -eq 0) {
        throw "Provide an allowed git subcommand after 'git'. Example: .\GitTools.ps1 git status -sb"
    }

    $gitSubcommand = Get-GitSubcommandFromArgs -GitArguments $ArgsForCommand

    if ([string]::IsNullOrWhiteSpace($gitSubcommand)) {
        throw "Could not determine the git subcommand."
    }

    if (-not $script:AllowedGitSubcommands.ContainsKey($gitSubcommand)) {
        throw "Git pass-through subcommand '$gitSubcommand' is not in the allowed common-git list. Use one of the named script commands or update AllowedGitSubcommands."
    }

    Invoke-Git -GitArguments $ArgsForCommand
}

function Resolve-GitToolCommand {
    param([AllowNull()][string]$Name)

    if ([string]::IsNullOrWhiteSpace($Name)) { return '' }

    $key = $Name.Trim().ToLowerInvariant()
    if ($script:CommandAliases.ContainsKey($key)) {
        return $script:CommandAliases[$key]
    }

    return $key
}

function Invoke-GitTool {
    param(
        [AllowNull()]
        [string]$Name,

        [AllowNull()]
        [string[]]$ArgsForCommand
    )

    $resolved = Resolve-GitToolCommand -Name $Name

    if ([string]::IsNullOrWhiteSpace($resolved) -or $resolved -in @('help', '-h', '--help', '/?')) {
        Show-GitToolHelp
        return
    }

    if ($script:SimpleGitCommands.ContainsKey($resolved)) {
        Invoke-GitSubcommand -Subcommand $script:SimpleGitCommands[$resolved] -ArgsForSubcommand $ArgsForCommand
        return
    }

    switch ($resolved) {
        'amend'          { Invoke-GitAmend -ArgsForCommand $ArgsForCommand; return }
        'graph'          { Invoke-GitGraph -ArgsForCommand $ArgsForCommand; return }
        'current-branch' { Invoke-GitCurrentBranch -ArgsForCommand $ArgsForCommand; return }
        'root'           { Invoke-GitRoot -ArgsForCommand $ArgsForCommand; return }
        'last'           { Invoke-GitLast -ArgsForCommand $ArgsForCommand; return }
        'unstage'        { Invoke-GitUnstage -ArgsForCommand $ArgsForCommand; return }
        'discard'        { Invoke-GitDiscard -ArgsForCommand $ArgsForCommand; return }
        'pr'             { Invoke-PullRequestCommand -ArgsForCommand $ArgsForCommand; return }
        'pr-draft'       { Invoke-PullRequestDraft -ArgsForCommand $ArgsForCommand; return }
        'make-draft'     { Invoke-PullRequestMakeDraft -ArgsForCommand $ArgsForCommand; return }
        'git'            { Invoke-GitPassThrough -ArgsForCommand $ArgsForCommand; return }
        default {
            throw "Unknown command '$Name'. Run .\GitTools.ps1 help for the command list."
        }
    }
}

$script:SimpleGitCommands = @{
    'add'          = 'add'
    'am'           = 'am'
    'apply'        = 'apply'
    'archive'      = 'archive'
    'bisect'       = 'bisect'
    'blame'        = 'blame'
    'branch'       = 'branch'
    'bundle'       = 'bundle'
    'checkout'     = 'checkout'
    'cherry-pick'  = 'cherry-pick'
    'clean'        = 'clean'
    'clone'        = 'clone'
    'commit'       = 'commit'
    'config'       = 'config'
    'describe'     = 'describe'
    'diff'         = 'diff'
    'difftool'     = 'difftool'
    'fetch'        = 'fetch'
    'format-patch' = 'format-patch'
    'fsck'         = 'fsck'
    'gc'           = 'gc'
    'grep'         = 'grep'
    'init'         = 'init'
    'lfs'          = 'lfs'
    'log'          = 'log'
    'merge'        = 'merge'
    'mergetool'    = 'mergetool'
    'mv'           = 'mv'
    'notes'        = 'notes'
    'pull'         = 'pull'
    'push'         = 'push'
    'range-diff'   = 'range-diff'
    'rebase'       = 'rebase'
    'reflog'       = 'reflog'
    'remote'       = 'remote'
    'reset'        = 'reset'
    'restore'      = 'restore'
    'revert'       = 'revert'
    'rm'           = 'rm'
    'shortlog'     = 'shortlog'
    'show'         = 'show'
    'show-branch'  = 'show-branch'
    'stash'        = 'stash'
    'status'       = 'status'
    'submodule'    = 'submodule'
    'switch'       = 'switch'
    'tag'          = 'tag'
    'version'      = 'version'
    'worktree'     = 'worktree'
    'ls-files'     = 'ls-files'
}

$script:AllowedGitSubcommands = @{}
foreach ($gitSubcommandName in @(
    'add', 'am', 'apply', 'archive', 'bisect', 'blame', 'branch', 'bundle',
    'checkout', 'cherry-pick', 'clean', 'clone', 'commit', 'config', 'describe',
    'diff', 'difftool', 'fetch', 'format-patch', 'fsck', 'gc', 'grep', 'help',
    'init', 'lfs', 'log', 'merge', 'mergetool', 'mv', 'notes', 'pull', 'push',
    'range-diff', 'rebase', 'reflog', 'remote', 'reset', 'restore', 'revert',
    'rm', 'shortlog', 'show', 'show-branch', 'stash', 'status', 'submodule',
    'switch', 'tag', 'version', 'worktree', 'ls-files'
)) {
    $script:AllowedGitSubcommands[$gitSubcommandName] = $true
}

$script:CommandAliases = @{
    's'              = 'status'
    'st'             = 'status'
    'stat'           = 'status'
    'ci'             = 'commit'
    'cm'             = 'commit'
    'co'             = 'checkout'
    'sw'             = 'switch'
    'br'             = 'branch'
    'branches'       = 'branch'
    'cp'             = 'cherry-pick'
    'cherrypick'     = 'cherry-pick'
    'df'             = 'diff'
    'lg'             = 'log'
    'hist'           = 'log'
    'remotes'        = 'remote'
    'tags'           = 'tag'
    'submodules'     = 'submodule'
    'wt'             = 'worktree'
    'ls'             = 'ls-files'
    'root-dir'       = 'root'
    'top'            = 'root'
    'branch-current' = 'current-branch'
    'draft-pr'       = 'pr-draft'
    'pr-new'         = 'pr-draft'
    'pr-draft'       = 'pr-draft'
}

# Dot-sourcing should only expose functions and data; it should not run a command.
if ($MyInvocation.InvocationName -eq '.') {
    return
}

try {
    Invoke-GitTool -Name $Command -ArgsForCommand $Remaining
}
catch {
    $script:GitToolExitCode = 1
    Write-Error $_.Exception.Message
}

exit $script:GitToolExitCode
