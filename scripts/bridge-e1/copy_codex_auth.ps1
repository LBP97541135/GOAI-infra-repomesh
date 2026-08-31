<#
.SYNOPSIS
    D-10: copy one already logged-in codex auth.json into each member's codex-home.

.DESCRIPTION
    A Bridge uses its own CODEX_HOME, one per member, and never the operator's
    ~/.codex. Six members would otherwise mean six interactive `codex login`
    rounds; D-10 copies the credential instead.

    The destinations are not derived here. This script asks e1_config.py, which
    calls the Bridge's own session_root(), so the paths move if and only if the
    product's do:

        <state dir>\sessions\<agentId>\codex-home\auth.json

    where <state dir> defaults to %LOCALAPPDATA%\repomesh-agent-bridge
    (instance_lock.default_state_dir). Pass -StateDir only if the run scripts
    will also pass --state-dir; the two must agree or the Bridge looks somewhere
    else.

    Only auth.json is copied. config.toml in particular is left alone: the
    Bridge writes a managed block into it when governed execution is turned on
    (runner_consumer.prepare_governed_codex_home) and copying one member's over
    another's would carry that member's trusted-project state with it.

    Run this BEFORE the first start_members.ps1. prepare_session_dirs puts the
    Low integrity label on codex-home (and on everything in it) at the first
    ensure_ready, so a file placed there beforehand is relabelled by the Bridge
    itself; a file dropped in afterwards keeps the operator's Medium label and
    the restricted child cannot rewrite it.

.EXAMPLE
    ./copy_codex_auth.ps1 -Members members.json `
        -SourceCodexHome "$env:LOCALAPPDATA\repomesh-agent-bridge\sessions\<logged-in id>\codex-home"
#>
param(
    [Parameter(Mandatory = $true)][string]$Members,
    [Parameter(Mandatory = $true)][string]$SourceCodexHome,
    [string]$StateDir,
    [string]$Subset,
    [string]$Python,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if (-not $Python) {
    $Python = Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "python interpreter not found: $Python (pass -Python)"
}

$source = Join-Path $SourceCodexHome "auth.json"
if (-not (Test-Path -LiteralPath $source)) {
    throw "no auth.json in $SourceCodexHome. Log in first: `$env:CODEX_HOME='$SourceCodexHome'; codex login"
}

$roster = Get-Content -Raw -LiteralPath $Members | ConvertFrom-Json
$selected = $roster.members
if ($Subset) {
    $selected = @($roster.members | Where-Object { $_.subsets -contains $Subset })
    if ($selected.Count -eq 0) {
        throw "no roster member is tagged '$Subset'"
    }
}

$configArgs = @((Join-Path $PSScriptRoot "e1_config.py"), "--members", $Members, "--codex-homes")
if ($StateDir) { $configArgs += @("--state-dir", $StateDir) }
$homes = (& $Python @configArgs | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) {
    throw "e1_config.py --codex-homes failed"
}

foreach ($member in $selected) {
    $codexHome = $homes.($member.key).codexHome
    $destination = Join-Path $codexHome "auth.json"
    if ((Test-Path -LiteralPath $destination) -and -not $Force) {
        Write-Host ("skipped  {0,-14} auth.json already present (pass -Force to overwrite)" -f $member.key)
        continue
    }
    New-Item -ItemType Directory -Path $codexHome -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Force
    Write-Host ("copied   {0,-14} -> {1}" -f $member.key, $destination)
}
