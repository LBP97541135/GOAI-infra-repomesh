<#
.SYNOPSIS
    Start every configured RepoMesh External Codex member with one command.

.DESCRIPTION
    This is the operator-facing wrapper around bridge-e1/start_members.ps1. It
    starts already-provisioned external members only: no account, Team,
    enrollment, database, or controller state is created here.

    Worker processes all receive the same control-plane workspace root. Leader
    processes never receive --workspace-root and therefore cannot enter the
    coding execution path. Secrets are loaded by start_members.ps1 from the
    gitignored env file and are never printed.

.EXAMPLE
    powershell -NoProfile -File .\scripts\start-local-cli.ps1

.EXAMPLE
    powershell -NoProfile -File .\scripts\start-local-cli.ps1 -Subset m7 -DryRun
#>
[CmdletBinding()]
param(
    [string]$Members,
    [string]$EnrollmentDir,
    [string]$EnvFile,
    [string]$RuntimeDir,
    [string]$WorkspaceRoot,
    [string]$StateDir,
    [string]$Subset,
    [string]$Python,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

function Resolve-RepoPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Path))
}

if (-not $Members) { $Members = "scripts\bridge-e1\members.json" }
if (-not $EnrollmentDir) { $EnrollmentDir = "output\bridge-team\e1\enrollments" }
if (-not $EnvFile) { $EnvFile = "output\bridge-team\e1-members.env" }
if (-not $RuntimeDir) { $RuntimeDir = "output\bridge-team\e1" }
if (-not $Python) { $Python = ".venv\Scripts\python.exe" }

$Members = Resolve-RepoPath $Members
$EnrollmentDir = Resolve-RepoPath $EnrollmentDir
$EnvFile = Resolve-RepoPath $EnvFile
$RuntimeDir = Resolve-RepoPath $RuntimeDir
$Python = Resolve-RepoPath $Python
if ($StateDir) { $StateDir = Resolve-RepoPath $StateDir }

if (-not $WorkspaceRoot) {
    $WorkspaceRoot = if ($env:REPOMESH_RUNNER_WORKSPACE_ROOT) {
        $env:REPOMESH_RUNNER_WORKSPACE_ROOT
    } else {
        Join-Path (Split-Path $repoRoot -Parent) ".repomesh-e1\workspaces"
    }
}
$WorkspaceRoot = [System.IO.Path]::GetFullPath($WorkspaceRoot)

$required = [ordered]@{
    "member roster" = $Members
    "enrollment directory" = $EnrollmentDir
    "credential env file" = $EnvFile
    "Python interpreter" = $Python
}
foreach ($entry in $required.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $entry.Value)) {
        throw "$($entry.Key) not found: $($entry.Value). Complete scripts\bridge-e1\README.md steps 1-8 first."
    }
}

if (-not $DryRun) {
    New-Item -ItemType Directory -Path $WorkspaceRoot -Force | Out-Null
}

$startScript = Join-Path $repoRoot "scripts\bridge-e1\start_members.ps1"
$arguments = @{
    Members = $Members
    EnrollmentDir = $EnrollmentDir
    EnvFile = $EnvFile
    PidDir = (Join-Path $RuntimeDir "pids")
    LogDir = (Join-Path $RuntimeDir "logs")
    WorkspaceRoot = $WorkspaceRoot
    Python = $Python
    DryRun = $DryRun
}
if ($StateDir) { $arguments.StateDir = $StateDir }
if ($Subset) { $arguments.Subset = $Subset }

Write-Host "RepoMesh local CLI"
Write-Host "  members:       $Members"
Write-Host "  workspaceRoot: $WorkspaceRoot"
Write-Host "  runtimeDir:    $RuntimeDir"
if ($Subset) { Write-Host "  subset:        $Subset" }
if ($DryRun) { Write-Host "  mode:          dry-run (no Bridge process will start)" }

& $startScript @arguments

if (-not $DryRun) {
    Write-Host "Local CLI members started. PID and log files are under $RuntimeDir."
}
