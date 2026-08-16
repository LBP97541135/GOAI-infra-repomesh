# Symmetric teardown for scripts/dev-up.ps1 (batch S-1).
#
# Only components this repository's dev-up started are considered, and each one
# is confirmed before it is stopped. State lives in .repomesh-dev/: a pid file
# per process, a marker file for the postgres container. No state file, no
# action - a service you started yourself is never touched.

#Requires -Version 5.1

[CmdletBinding()]
param(
    # 不逐项询问，直接停（仍然只停本仓 dev-up 起的组件）
    [switch] $Yes
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $RepoRoot
$StateDir = Join-Path $RepoRoot '.repomesh-dev'

function Write-Ok { param([string] $Text) Write-Host "   [OK]   $Text" }
function Write-Skip { param([string] $Text) Write-Host "   [跳过] $Text" }
function Write-Info { param([string] $Text) Write-Host "          $Text" }

function Confirm-Stop {
    param([string] $Label)
    if ($Yes) { return $true }
    $answer = Read-Host "   停止 $Label？[y/N]"
    return $answer -in @('y', 'Y')
}

function Get-LiveProcess {
    param([int] $ProcessId)
    return Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
}

function Stop-TrackedProcess {
    param([string] $Label, [string] $FileName)
    $file = Join-Path $StateDir $FileName
    if (-not (Test-Path $file)) {
        Write-Skip "没有 $Label 的记录，不动它（若它在跑，那不是本脚本起的）。"
        return
    }
    $raw = (Get-Content $file -ErrorAction SilentlyContinue | Select-Object -First 1)
    $processId = 0
    if (-not [int]::TryParse(($raw | Out-String).Trim(), [ref] $processId) -or -not (Get-LiveProcess $processId)) {
        Write-Skip "$Label（PID $raw）已经不在了，清掉记录。"
        Remove-Item $file -ErrorAction SilentlyContinue
        return
    }
    if (-not (Confirm-Stop "$Label（PID $processId）")) {
        Write-Skip "$Label 保留。"
        return
    }
    # uv / npm wrap the real server in a child process; kill the whole tree.
    taskkill /PID $processId /T /F *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue }
    if (Get-LiveProcess $processId) {
        Write-Host "   [失败] 没能停掉 $Label（PID $processId）。" -ForegroundColor Red
        Write-Info "手工处理： taskkill /PID $processId /T /F"
    } else {
        Remove-Item $file -ErrorAction SilentlyContinue
        Write-Ok "$Label 已停止。"
    }
}

Write-Host 'RepoMesh 交付控制台 · 收摊'
Write-Host "状态目录：$StateDir`n"

Stop-TrackedProcess '前端 vite（5280）' 'frontend.pid'
Stop-TrackedProcess '后端 uvicorn（8100）' 'backend.pid'

$marker = Join-Path $StateDir 'postgres.started'
if (Test-Path $marker) {
    $dockerUp = $false
    if (Get-Command 'docker' -ErrorAction SilentlyContinue) {
        docker info *> $null
        $dockerUp = ($LASTEXITCODE -eq 0)
    }
    if (-not $dockerUp) {
        Write-Skip 'Docker 没在跑，postgres 记录保留，等 Docker 起来再收。'
    } elseif (Confirm-Stop 'compose 的 postgres 容器（数据卷保留）') {
        docker compose stop postgres *> $null
        if ($LASTEXITCODE -eq 0) {
            Remove-Item $marker -ErrorAction SilentlyContinue
            Write-Ok 'postgres 容器已停止（卷 repomesh-postgres 保留，下次 dev-up 数据还在）。'
            Write-Info '要连数据一起删： docker compose down -v'
        } else {
            Write-Host '   [失败] docker compose stop postgres 没成功。' -ForegroundColor Red
            Write-Info '看一眼： docker compose ps'
        }
    } else {
        Write-Skip 'postgres 保留。'
    }
} else {
    Write-Skip '没有 postgres 的记录，不动任何数据库容器。'
}

Write-Host "`n收摊结束。日志留在 $StateDir，重来一次： .\scripts\dev-up.ps1"
