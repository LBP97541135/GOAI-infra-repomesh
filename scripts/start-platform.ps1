param(
    [switch]$InstallAgentTeams,
    [switch]$SkipBackend
)

# ErrorActionPreference must stay "Continue" on Windows PowerShell 5.1: docker
# (and docker compose) write progress/status to stderr, and under EAP=Stop PS 5.1
# converts those lines into a terminating NativeCommandError -- even with 2>&1
# redirection. The script already detects native failures via $LASTEXITCODE and
# aborts with explicit throw statements, so Continue is the correct setting here.
# On PowerShell 7+ the PS7-only $PSNativeCommandUseErrorActionPreference=$false
# would achieve the same effect while keeping Stop.
$ErrorActionPreference = "Continue"
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepositoryRoot
$ApiPort = if ($env:REPOMESH_API_PORT) { $env:REPOMESH_API_PORT } else { "8000" }
$WebPort = if ($env:REPOMESH_WEB_PORT) { $env:REPOMESH_WEB_PORT } else { "5280" }

# Load the whole .env into the process environment before anything downstream
# reads it. Without this, only the three variables Get-RepoMeshEnvValue names
# below ever reach the AgentTeams installer subprocess -- AGENTTEAMS_NON_INTERACTIVE=1,
# AGENTTEAMS_VERSION, AGENTTEAMS_MATRIX_APPSERVICE_ENABLED and friends in
# .env.example sit unread, so the "one-command" install falls into the
# installer's interactive prompts instead of running unattended. A variable
# already set on this process (e.g. `$env:FOO = "bar"` before invoking this
# script) is left alone.
$DotEnv = Join-Path $RepositoryRoot ".env"
if (Test-Path $DotEnv) {
    foreach ($Line in Get-Content $DotEnv) {
        if ($Line -match '^\s*#' -or $Line -notmatch '^\s*([^=\s]+)\s*=\s*(.*)$') { continue }
        $Name = $Matches[1]
        $Value = $Matches[2].Trim().Trim('"').Trim("'")
        if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name))) {
            [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
        }
    }
}

# One product-level model connection feeds both processes. Component-specific
# variables remain supported as explicit advanced overrides.
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
    # Create()/GetBytes, not the static ::Fill — the latter is .NET Core only
    # and this script must run under Windows PowerShell 5.1 (.NET Framework).
    $Rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Rng.GetBytes($Bytes)
    } finally {
        $Rng.Dispose()
    }
    return [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function New-FernetKey {
    $Bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($Bytes)
    return [Convert]::ToBase64String($Bytes).Replace('+', '-').Replace('/', '_')
}

# PS 5.1-compatible equivalent of `Set-Content -Encoding utf8NoBOM` (which
# only exists in PowerShell 7+). Joins the given lines and writes them without
# a BOM so downstream env parsers (Get-Content / regex key=value matching)
# behave identically on both PowerShell editions.
function Set-Utf8NoBom([string]$Path, [string[]]$Lines) {
    $Content = ($Lines -join "`r`n")
    if ($Content -ne "") { $Content += "`r`n" }
    [System.IO.File]::WriteAllText($Path, $Content, (New-Object System.Text.UTF8Encoding($false)))
}

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
$ControllerContainer = if ($env:REPOMESH_AGENTTEAMS_CONTROLLER_CONTAINER) {
    $env:REPOMESH_AGENTTEAMS_CONTROLLER_CONTAINER
} else {
    "agentteams-controller"
}
$PlatformSecretFile = Join-Path $SecretDirectory "platform.env"
$RuntimeFile = Join-Path $SecretDirectory "platform-runtime.env"
$AgentTeamsSourceEnv = if ($env:AGENTTEAMS_ENV_FILE) {
    $env:AGENTTEAMS_ENV_FILE
} else {
    Join-Path $HOME "agentteams-manager.env"
}
$BootstrapAgentTeamsEnv = Join-Path $SecretDirectory "agentteams-manager.env"
New-Item -ItemType Directory -Force $SecretDirectory | Out-Null
$CredentialKeyFile = Join-Path $SecretDirectory "platform-credentials.key"
if ([string]::IsNullOrWhiteSpace($env:REPOMESH_CREDENTIALS_ENCRYPTION_KEY) -and
    -not (Test-Path $CredentialKeyFile)) {
    Set-Utf8NoBom $CredentialKeyFile @(New-FernetKey)
}
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
Set-Utf8NoBom $PlatformSecretFile @(
    $PersistedSecrets.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }
)
Set-Utf8NoBom (Join-Path $SecretDirectory "browser-action-token") $PersistedSecrets["REPOMESH_AGENT_ACTION_TOKEN"]

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

docker compose up -d postgres 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL failed to start."
}

docker exec $ControllerContainer curl -sf http://127.0.0.1:8090/healthz *> $null
$AgentTeamsReady = ($LASTEXITCODE -eq 0)
$ModelConfigured = -not [string]::IsNullOrWhiteSpace($env:AGENTTEAMS_LLM_API_KEY)
if ($InstallAgentTeams -or (-not $AgentTeamsReady -and $ModelConfigured)) {
    if (-not $AgentTeamsReady) {
        Write-Host "AgentTeams Controller is missing; installing it automatically."
    }
    $Installer = Join-Path $RepositoryRoot "components/agentteams/install/agentteams-install.ps1"
    & $Installer -NonInteractive
    if ($LASTEXITCODE -ne 0) {
        throw "AgentTeams installation failed."
    }
    docker exec $ControllerContainer curl -sf http://127.0.0.1:8090/healthz *> $null
    $AgentTeamsReady = ($LASTEXITCODE -eq 0)
} elseif (-not $AgentTeamsReady) {
    Write-Host "Model credentials are not configured; starting the setup plane first."
    $env:REPOMESH_AGENTTEAMS_REQUIRED = "false"
    Remove-Item Env:REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT -ErrorAction SilentlyContinue
    Remove-Item Env:REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY -ErrorAction SilentlyContinue
    Remove-Item $RuntimeFile -ErrorAction SilentlyContinue
}

if ($ModelConfigured -and -not $AgentTeamsReady) {
    throw "AgentTeams Controller is not ready after automatic installation."
}

if ($SkipBackend) {
    exit 0
}

$InjectedControllerToken = $false
if ($AgentTeamsReady -and [string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN)) {
    $ControllerToken = docker exec $ControllerContainer cat /var/run/agentteams/cli-token 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ControllerToken)) {
        throw "AgentTeams Controller token could not be loaded."
    }
    $env:REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN = $ControllerToken.Trim()
    $InjectedControllerToken = $true
}

if ($AgentTeamsReady -and [string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN)) {
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
            $LoginResult = $LoginBody | docker exec -i $ControllerContainer curl -sf `
                -X POST http://127.0.0.1:6167/_matrix/client/v3/login `
                -H "Content-Type: application/json" -d '@-' 2>$null
            if ($LASTEXITCODE -eq 0 -and $LoginResult) {
                $env:REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN = `
                    ($LoginResult | ConvertFrom-Json).access_token
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN)) {
        # Not fatal: the API can still serve the read model without a messenger. But
        # be loud, because materialize and task dispatch will 503 until this is set.
        Write-Warning "no AgentTeams admin credentials found ($AgentTeamsEnv)."
        Write-Warning "starting the API without a Matrix messenger -- materialize and task dispatch will return 503 until REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN is set."
    }
}

# Task packages reach the worker through AgentTeams' MinIO (S3): the worker runs
# `mc mirror agentteams/<bucket>/teams/.../shared/tasks/...` to pull them. The API
# must therefore publish through the S3 object publisher, which the bootstrap only
# selects when endpoint + access key + secret key are all set. Left unset, it falls
# back to the disk publisher, whose plain files MinIO's S3 API does not serve -- the
# worker's mirror then finds nothing and no task ever reaches an agent. Derive the
# endpoint (reachable on the shared agentteams-net) and MinIO root credentials from
# the manager env, mirroring the Matrix token injection above (ports start-platform.sh:120-147).
if ($AgentTeamsReady -and (
    [string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY) -or
    [string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY))) {
    $AgentTeamsEnv = if ($env:AGENTTEAMS_ENV_FILE) {
        $env:AGENTTEAMS_ENV_FILE
    } else {
        Join-Path $HOME "agentteams-manager.env"
    }
    $MinioUser = $null
    $MinioPassword = $null
    if (Test-Path $AgentTeamsEnv) {
        $MinioUser = (Get-Content $AgentTeamsEnv | Where-Object {
            $_ -match '^AGENTTEAMS_MINIO_USER='
        } | Select-Object -Last 1) -replace '^AGENTTEAMS_MINIO_USER=', ''
        $MinioPassword = (Get-Content $AgentTeamsEnv | Where-Object {
            $_ -match '^AGENTTEAMS_MINIO_PASSWORD='
        } | Select-Object -Last 1) -replace '^AGENTTEAMS_MINIO_PASSWORD=', ''
    }
    if ($MinioUser -and $MinioPassword) {
        if ([string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT)) {
            $env:REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT = "http://agentteams-controller:9000"
        }
        $env:REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY = $MinioUser
        $env:REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY = $MinioPassword
    } else {
        # Not fatal: the API still serves the read model. But task dispatch will not
        # reach any worker, because the disk publisher's files are invisible over S3.
        Write-Warning "no AgentTeams MinIO credentials found ($AgentTeamsEnv)."
        Write-Warning "the API will fall back to the disk task publisher, whose files the worker's S3 mirror cannot read -- dispatched tasks never reach workers."
    }
}

if ($AgentTeamsReady -and (Test-Path $AgentTeamsSourceEnv)) {
    Copy-Item -Force $AgentTeamsSourceEnv $BootstrapAgentTeamsEnv
}
if ($AgentTeamsReady -and
    -not [string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN) -and
    -not [string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN) -and
    -not [string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY) -and
    -not [string]::IsNullOrWhiteSpace($env:REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY)) {
    $RuntimeTemporary = "$RuntimeFile.tmp"
    @(
        "REPOMESH_AGENTTEAMS_REQUIRED=true"
        "REPOMESH_AGENTTEAMS_CONTROLLER_URL=http://agentteams-controller:8090"
        "REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN=$env:REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN"
        "REPOMESH_AGENTTEAMS_MATRIX_URL=http://agentteams-controller:6167"
        "REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN=$env:REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN"
        "REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT=http://agentteams-controller:9000"
        "REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY=$env:REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY"
        "REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY=$env:REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY"
        "REPOMESH_AGENTTEAMS_STORAGE_BUCKET=agentteams-storage"
    ) | Set-Utf8NoBom $RuntimeTemporary
    Move-Item -Force $RuntimeTemporary $RuntimeFile
}

try {
    docker compose --profile platform up -d --build api web bootstrap 2>&1 | Out-Null
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
        $Health = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri "http://127.0.0.1:$ApiPort/health/ready"
        if ($Health.StatusCode -eq 200) {
            $Ready = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

if (-not $Ready) {
    docker compose --profile platform logs --tail 100 api 2>&1
    throw "RepoMesh API did not become ready at http://127.0.0.1:$ApiPort."
}

$WebReady = $false
foreach ($Attempt in 1..30) {
    try {
        $Page = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri "http://127.0.0.1:$WebPort/"
        if ($Page.StatusCode -eq 200) {
            $WebReady = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $WebReady) {
    docker compose --profile platform logs --tail 100 web 2>&1
    throw "RepoMesh console did not become ready at http://127.0.0.1:$WebPort."
}

$BootstrapReady = $false
$BootstrapContainer = docker compose --profile platform ps -q bootstrap 2>&1
foreach ($Attempt in 1..30) {
    if ($BootstrapContainer) {
        $BootstrapHealth = docker inspect --format '{{.State.Health.Status}}' $BootstrapContainer 2>$null
        if ($BootstrapHealth -eq "healthy") {
            $BootstrapReady = $true
            break
        }
    }
    Start-Sleep -Seconds 2
}
if (-not $BootstrapReady) {
    docker compose --profile platform logs --tail 100 bootstrap 2>&1
    throw "RepoMesh bootstrap reconciler did not become ready."
}

Write-Host "RepoMesh is ready at http://127.0.0.1:$ApiPort/docs"
Write-Host "RepoMesh console is ready at http://127.0.0.1:$WebPort"
