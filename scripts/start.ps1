#Requires -Version 5.1

[CmdletBinding()]
param([switch] $NoBrowser)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepositoryRoot
$SecretDirectory = if ($env:REPOMESH_SECRETS_DIR) {
    if ([System.IO.Path]::IsPathRooted($env:REPOMESH_SECRETS_DIR)) {
        [System.IO.Path]::GetFullPath($env:REPOMESH_SECRETS_DIR)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $RepositoryRoot $env:REPOMESH_SECRETS_DIR))
    }
} else {
    Join-Path $RepositoryRoot ".secrets"
}
$env:REPOMESH_SECRETS_DIR = $SecretDirectory
$StartupEnv = Join-Path $SecretDirectory "startup.env"
New-Item -ItemType Directory -Force $SecretDirectory | Out-Null
if (Test-Path $StartupEnv) {
    foreach ($Line in Get-Content $StartupEnv) {
        if ($Line -match '^([^#=]+)=(.*)$' -and
            [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Matches[1]))) {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
        }
    }
}

function Test-PortAvailable([int] $Port) {
    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    return -not ($listeners | Where-Object { $_.Port -eq $Port })
}

function Select-Port([int] $Preferred, [int] $Last) {
    foreach ($Port in $Preferred..$Last) {
        if (Test-PortAvailable $Port) { return $Port }
    }
    throw "No available port in range $Preferred-$Last."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required. Install Docker Desktop, start it, then rerun this launcher."
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker is installed but not running. Start Docker Desktop, then rerun this launcher."
}

if (-not $env:REPOMESH_POSTGRES_PORT) {
    $env:REPOMESH_POSTGRES_PORT = "$(Select-Port 5432 5442)"
}
if (-not $env:REPOMESH_API_PORT) {
    $env:REPOMESH_API_PORT = "$(Select-Port 8000 8010)"
}
if (-not $env:REPOMESH_WEB_PORT) {
    $env:REPOMESH_WEB_PORT = "$(Select-Port 5280 5290)"
}
@(
    "REPOMESH_POSTGRES_PORT=$env:REPOMESH_POSTGRES_PORT"
    "REPOMESH_API_PORT=$env:REPOMESH_API_PORT"
    "REPOMESH_WEB_PORT=$env:REPOMESH_WEB_PORT"
) | Set-Content -Encoding utf8NoBOM $StartupEnv

& (Join-Path $PSScriptRoot "start-platform.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$ConsoleUrl = "http://127.0.0.1:$env:REPOMESH_WEB_PORT"
if (-not $NoBrowser) {
    Start-Process $ConsoleUrl
}
Write-Host "Open RepoMesh: $ConsoleUrl"
