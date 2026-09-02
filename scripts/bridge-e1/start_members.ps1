<#
.SYNOPSIS
    Start one Bridge process per roster member and record its PID.

.DESCRIPTION
    One process per member, because the instance lock is per worker identity:
    a second process for the same member exits 3 rather than sharing the rooms.

    A worker is started with --workspace-root and a repository leader without
    it, and that asymmetry is not a convention this script invented. The Bridge
    refuses --workspace-root for a leader outright (cli._governed_workspace_root,
    AC-02): a leader decides and does not code, so the flag is not something it
    may be given by a copied command line. By default each worker uses the root
    from the roster. -WorkspaceRoot deliberately overrides every worker with the
    control plane's shared root, while leaders still receive no repository.

    Credentials reach each process through the environment, named by the
    enrollment's env: locators. -EnvFile loads NAME=value lines into this
    session before launching so the children inherit them; nothing is echoed.

    Stopping is stop_members.ps1, which reads the same PID files. Do not reach
    for `pkill -f` from Git Bash: it does not kill a process started this way.

    -Subset selects by roster tag and -Only selects one member by key. They are
    different questions: a subset is a scenario an operator runs, while a single
    key is what recovering one failed member needs -- and what the Local Launcher
    needs on every start, because the already-live check below throws rather than
    skips and would abandon the rest of the batch on a second click.

.EXAMPLE
    ./start_members.ps1 -Members members.json -EnrollmentDir out\enrollments `
        -EnvFile ..\..\output\bridge-team\e1-members.env -PidDir out\pids -LogDir out\logs -Subset m7

.EXAMPLE
    ./start_members.ps1 -Members members.json -EnrollmentDir out\enrollments `
        -PidDir out\pids -Only alpha-worker
#>
param(
    [Parameter(Mandatory = $true)][string]$Members,
    [Parameter(Mandatory = $true)][string]$EnrollmentDir,
    [Parameter(Mandatory = $true)][string]$PidDir,
    [string]$LogDir,
    [string]$EnvFile,
    [string]$StateDir,
    [string]$WorkspaceRoot,
    [string]$Subset,
    [string]$Only,
    [string]$Python,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not $Python) {
    $Python = Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "python interpreter not found: $Python (pass -Python)"
}
if (-not $LogDir) { $LogDir = $PidDir }

if ($EnvFile) {
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        throw "env file not found: $EnvFile"
    }
    $loaded = 0
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $split = $trimmed.IndexOf("=")
        if ($split -lt 1) { throw "malformed line in ${EnvFile}: expected NAME=value" }
        $name = $trimmed.Substring(0, $split).Trim()
        $value = $trimmed.Substring($split + 1)
        Set-Item -Path ("Env:" + $name) -Value $value
        $loaded += 1
    }
    Write-Host "loaded $loaded variable(s) from $EnvFile"
}

$roster = Get-Content -Raw -LiteralPath $Members | ConvertFrom-Json
$selected = $roster.members
if ($Subset) {
    $selected = @($roster.members | Where-Object { $_.subsets -contains $Subset })
    if ($selected.Count -eq 0) {
        throw "no roster member is tagged '$Subset'"
    }
}
if ($Only) {
    $selected = @($selected | Where-Object { $_.key -eq $Only })
    if ($selected.Count -eq 0) {
        throw "no roster member has the key '$Only'"
    }
}

New-Item -ItemType Directory -Path $PidDir -Force | Out-Null
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

foreach ($member in $selected) {
    $enrollment = Join-Path $EnrollmentDir ("enrollment.{0}.json" -f $member.key)
    if (-not (Test-Path -LiteralPath $enrollment)) {
        throw "enrollment not found: $enrollment (run make_enrollments.py first)"
    }

    $arguments = @("-m", "repomesh_agent_bridge", "run", "--enrollment", $enrollment)
    if ($StateDir) { $arguments += @("--state-dir", $StateDir) }
    if ($member.role -eq "worker") {
        $workerWorkspaceRoot = if ($WorkspaceRoot) { $WorkspaceRoot } else { [string]$member.workspaceRoot }
        if (-not $workerWorkspaceRoot) {
            throw "workspace root is required for worker $($member.key)"
        }
        if (-not $DryRun -and -not (Test-Path -LiteralPath $workerWorkspaceRoot -PathType Container)) {
            throw "workspace root is not an existing directory: $workerWorkspaceRoot"
        }
        $arguments += @("--workspace-root", $workerWorkspaceRoot)
    }

    if ($DryRun) {
        Write-Host ("would run {0,-14} {1} {2}" -f $member.key, $Python, ($arguments -join " "))
        continue
    }

    $pidFile = Join-Path $PidDir ("{0}.pid" -f $member.key)
    if (Test-Path -LiteralPath $pidFile) {
        $existing = Get-Content -LiteralPath $pidFile | Select-Object -First 1
        if (Get-Process -Id $existing -ErrorAction SilentlyContinue) {
            throw "$($member.key) already has a live process ($existing); stop it first"
        }
        Remove-Item -LiteralPath $pidFile
    }

    $process = Start-Process -FilePath $Python -ArgumentList $arguments -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir ("{0}.out.log" -f $member.key)) `
        -RedirectStandardError (Join-Path $LogDir ("{0}.err.log" -f $member.key))
    Set-Content -LiteralPath $pidFile -Value $process.Id
    Write-Host ("started  {0,-14} {1,-19} pid={2}" -f $member.key, $member.role, $process.Id)
}
