param(
    [switch]$InstallAgentTeams,
    [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepositoryRoot

# One product-level model connection feeds both processes. Component-specific
# variables remain supported as explicit advanced overrides.
$DotEnv = Join-Path $RepositoryRoot ".env"
function Get-RepoMeshEnvValue([string]$Name) {
    $Current = [Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrWhiteSpace($Current)) {
        return $Current
    }
    if (-not (Test-Path $DotEnv)) {
        return $null
    }
    $Prefix = "$Name="
    $Line = Get-Content $DotEnv | Where-Object { $_.StartsWith($Prefix) } | Select-Object -Last 1
    if ($null -eq $Line) {
        return $null
    }
    return $Line.Substring($Prefix.Length).Trim().Trim('"').Trim("'")
}

function New-SecureToken {
    $Bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($Bytes)
    return [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

$SecretDirectory = Join-Path $RepositoryRoot ".secrets"
$PlatformSecretFile = Join-Path $SecretDirectory "platform.env"
New-Item -ItemType Directory -Force $SecretDirectory | Out-Null
$PersistedSecrets = @{}
if (Test-Path $PlatformSecretFile) {
    foreach ($Line in Get-Content $PlatformSecretFile) {
        if ($Line -match '^([^#=]+)=(.*)$') {
            $PersistedSecrets[$Matches[1]] = $Matches[2]
        }
    }
}
foreach ($Name in @(
    "REPOMESH_RUNNER_CONTROL_TOKEN",
    "REPOMESH_AGENT_ACTION_TOKEN",
    "REPOMESH_MCP_GATEWAY_TOKEN"
)) {
    $Value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($Value)) { $Value = $PersistedSecrets[$Name] }
    if ([string]::IsNullOrWhiteSpace($Value)) { $Value = New-SecureToken }
    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    $PersistedSecrets[$Name] = $Value
}
$PersistedSecrets.GetEnumerator() | Sort-Object Name | ForEach-Object {
    "$($_.Name)=$($_.Value)"
} | Set-Content -Encoding utf8NoBOM $PlatformSecretFile

if ([string]::IsNullOrWhiteSpace($env:AGENTTEAMS_LLM_API_KEY)) {
    $env:AGENTTEAMS_LLM_API_KEY = Get-RepoMeshEnvValue "REPOMESH_MODEL_API_KEY"
}
if ([string]::IsNullOrWhiteSpace($env:AGENTTEAMS_OPENAI_BASE_URL)) {
    $env:AGENTTEAMS_OPENAI_BASE_URL = Get-RepoMeshEnvValue "REPOMESH_MODEL_BASE_URL"
}
if ([string]::IsNullOrWhiteSpace($env:AGENTTEAMS_DEFAULT_MODEL)) {
    $env:AGENTTEAMS_DEFAULT_MODEL = Get-RepoMeshEnvValue "REPOMESH_MODEL"
}

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

$InjectedControllerToken = $false
if ([string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN)) {
    $ControllerToken = docker exec agentteams-controller cat /var/run/agentteams/cli-token
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ControllerToken)) {
        throw "AgentTeams Controller token could not be loaded."
    }
    $env:REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN = $ControllerToken.Trim()
    $InjectedControllerToken = $true
}

if ([string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN)) {
    $AgentTeamsEnv = if ($env:AGENTTEAMS_ENV_FILE) {
        $env:AGENTTEAMS_ENV_FILE
    } else {
        Join-Path $HOME "agentteams-manager.env"
    }
    if (Test-Path $AgentTeamsEnv) {
        $AdminUser = (Get-Content $AgentTeamsEnv | Where-Object {
            $_ -match '^AGENTTEAMS_ADMIN_USER='
        } | Select-Object -Last 1) -replace '^AGENTTEAMS_ADMIN_USER=', ''
        $AdminPassword = (Get-Content $AgentTeamsEnv | Where-Object {
            $_ -match '^AGENTTEAMS_ADMIN_PASSWORD='
        } | Select-Object -Last 1) -replace '^AGENTTEAMS_ADMIN_PASSWORD=', ''
        if ($AdminUser -and $AdminPassword) {
            $LoginBody = @{
                type = "m.login.password"
                identifier = @{ type = "m.id.user"; user = $AdminUser }
                password = $AdminPassword
            } | ConvertTo-Json -Compress
            $LoginResult = $LoginBody | docker exec -i agentteams-controller curl -sf `
                -X POST http://127.0.0.1:6167/_matrix/client/v3/login `
                -H "Content-Type: application/json" -d '@-'
            if ($LASTEXITCODE -eq 0 -and $LoginResult) {
                $env:REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN = `
                    ($LoginResult | ConvertFrom-Json).access_token
            }
        }
    }
}

try {
    docker compose --profile platform up -d --build api web
    $ComposeExitCode = $LASTEXITCODE
} finally {
    if ($InjectedControllerToken) {
        Remove-Item Env:REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN
    }
}
if ($ComposeExitCode -ne 0) {
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
Write-Host "RepoMesh Control Plane is ready at http://127.0.0.1:5173"
