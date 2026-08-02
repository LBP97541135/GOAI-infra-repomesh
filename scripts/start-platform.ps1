param(
    [switch]$InstallAgentTeams,
    [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepositoryRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required to start RepoMesh infrastructure."
}

docker compose up -d postgres
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL failed to start."
}

if ($InstallAgentTeams) {
    $PowerShell = Get-Command pwsh -ErrorAction SilentlyContinue
    if (-not $PowerShell) {
        throw "The checked-in AgentTeams installer requires PowerShell 7 or newer."
    }

    $Installer = Join-Path $RepositoryRoot "components/agentteams/install/agentteams-install.ps1"
    & $PowerShell.Source -NoProfile -File $Installer
    if ($LASTEXITCODE -ne 0) {
        throw "AgentTeams installation failed."
    }
}

docker exec agentteams-controller curl -sf http://127.0.0.1:8090/healthz *> $null
if ($LASTEXITCODE -ne 0) {
    throw "AgentTeams Controller is not ready. Run this script with -InstallAgentTeams."
}

if ($SkipBackend) {
    exit 0
}

docker compose --profile platform up -d --build api
if ($LASTEXITCODE -ne 0) {
    throw "RepoMesh API failed to start."
}

$Ready = $false
foreach ($Attempt in 1..30) {
    try {
        $Health = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri "http://127.0.0.1:8000/health/ready"
        if ($Health.StatusCode -eq 200) {
            $Ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $Ready) {
    docker compose --profile platform logs --tail 100 api
    throw "RepoMesh API did not become ready at http://127.0.0.1:8000."
}

Write-Host "RepoMesh is ready at http://127.0.0.1:8000/docs"