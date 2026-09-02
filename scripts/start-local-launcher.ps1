<#
.SYNOPSIS
    Run the RepoMesh Local Launcher on this machine's loopback address.

.DESCRIPTION
    The launcher is what lets the Console start and stop this machine's external
    Bridge members with a click. It is started by hand, by the operator, in a
    window they can watch: there is no installer and no service registration, so
    nothing keeps listening on this machine after they close it.

    It takes one argument, a JSON config naming the roster, the enrollment
    directory, the credential env file, the runtime directory, the roster version
    and the Console origins allowed to write. That file lives under the
    gitignored output\ and never enters the repository; the keys are documented
    in src\repomesh_local_launcher\config.py.

    Nothing here reads a credential. The launcher shells back to
    start-local-cli.ps1, which loads the env file itself and prints nothing from
    it, exactly as it does when an operator runs it directly.

.EXAMPLE
    powershell -NoProfile -File .\scripts\start-local-launcher.ps1

.EXAMPLE
    powershell -NoProfile -File .\scripts\start-local-launcher.ps1 -Config output\local-launcher\m7.json
#>
[CmdletBinding()]
param(
    [string]$Config,
    [string]$Python
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

if (-not $Config) { $Config = "output\local-launcher\config.json" }
if (-not $Python) { $Python = ".venv\Scripts\python.exe" }

$Config = Resolve-RepoPath $Config
$Python = Resolve-RepoPath $Python

$required = [ordered]@{
    "launcher config" = $Config
    "Python interpreter" = $Python
}
foreach ($entry in $required.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $entry.Value)) {
        throw "$($entry.Key) not found: $($entry.Value)"
    }
}

Write-Host "RepoMesh Local Launcher"
Write-Host "  config: $Config"

& $Python -m repomesh_local_launcher $Config
