# One-command development launcher for the delivery console (batch S-1/S-3/S-4).
#
# Stages, in order: compose postgres -> dependencies -> alembic -> API on 8100
# -> Vite on 5280 -> browser. Every stage probes first and skips whatever is
# already serving, so re-running the script is safe and cheap.
#
# Safety rules this script keeps, on purpose:
#   * it never restarts, migrates into, or stops anything it did not start;
#   * `alembic upgrade head` only runs against the database this script brought
#     up itself, or against a DSN the operator passed explicitly;
#   * a busy host port that is not ours aborts the run with instructions
#     instead of being adopted.
#
# Ports 8100 (API) and 5280 (Vite) are fixed by frontend/vite.config.ts and are
# not configurable here. The postgres host port is: REPOMESH_POSTGRES_PORT.

#Requires -Version 5.1

[CmdletBinding()]
param(
    # 起好之后灌演示种子（scripts/seed-console-demo.py），首屏不是空态
    [switch] $Seed,
    # 起好之后不自动打开浏览器
    [switch] $NoBrowser
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $RepoRoot

$StateDir = Join-Path $RepoRoot '.repomesh-dev'
$ApiPort = 8100
$WebPort = 5280
$DbPort = if ($env:REPOMESH_POSTGRES_PORT) { $env:REPOMESH_POSTGRES_PORT } else { '5432' }
$ActionToken = if ($env:REPOMESH_AGENT_ACTION_TOKEN) { $env:REPOMESH_AGENT_ACTION_TOKEN } else { 'console-dev-token' }
$ConsoleUrl = "http://127.0.0.1:$WebPort"

$DsnExplicit = [bool] $env:REPOMESH_DATABASE_URL
$Dsn = if ($DsnExplicit) { $env:REPOMESH_DATABASE_URL } else { "postgresql+asyncpg://repomesh:repomesh@127.0.0.1:$DbPort/repomesh" }

$script:OwnDatabase = $false
$script:Warnings = @()

function Write-Step { param([string] $Text) Write-Host "`n== $Text" }
function Write-Ok { param([string] $Text) Write-Host "   [OK]   $Text" }
function Write-Skip { param([string] $Text) Write-Host "   [跳过] $Text" }
function Write-Info { param([string] $Text) Write-Host "          $Text" }
function Write-Note {
    param([string] $Text)
    Write-Host "   [注意] $Text"
    $script:Warnings += $Text
}

function Stop-WithGuidance {
    param([string] $Reason, [string[]] $Guidance = @())
    Write-Host "`n   [失败] $Reason" -ForegroundColor Red
    foreach ($line in $Guidance) { Write-Host "          $line" -ForegroundColor Red }
    Write-Host "`n启动中止。已经起来的组件没有被回收，修好上面的问题后重跑本脚本即可（幂等）。"
    exit 1
}

function Test-Command { param([string] $Name) [bool] (Get-Command $Name -ErrorAction SilentlyContinue) }

# True when something is listening on the given local TCP port.
function Test-PortBusy {
    param([int] $Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.ConnectAsync('127.0.0.1', $Port)
        if (-not $connect.Wait(1500)) { return $false }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

# True when the URL answers with any HTTP status (404 still proves "serving").
# An HTTP error carries a Response object; a refused connection does not.
# Written for Windows PowerShell 5.1 as well, so no -SkipHttpErrorCheck.
function Test-HttpServing {
    param([string] $Url)
    try {
        $null = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return $true
    } catch {
        return ($null -ne $_.Exception.Response)
    }
}

# Same question, asked three times: a component that is up but momentarily
# reloading would otherwise be mistaken for a stranger holding its port, and
# that mistake aborts the run.
function Test-HttpServingSettled {
    param([string] $Url)
    foreach ($attempt in 1..3) {
        if (Test-HttpServing $Url) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Invoke-Checked {
    param([string] $File, [string[]] $Arguments, [string] $Reason, [string[]] $Guidance = @())
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { Stop-WithGuidance -Reason $Reason -Guidance $Guidance }
}

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

Write-Host 'RepoMesh 交付控制台 · 一键开发环境'
Write-Host "仓库根：$RepoRoot"
Write-Host ("数据库：$Dsn" + $(if ($DsnExplicit) { '（来自 REPOMESH_DATABASE_URL）' } else { '' }))

# ---------------------------------------------------------------- 1. 后端
Write-Step "[1/4] 后端 API（127.0.0.1:$ApiPort）"

$backendSkipped = $false
if (Test-HttpServingSettled "http://127.0.0.1:$ApiPort/docs") {
    $backendSkipped = $true
    Write-Skip "$ApiPort 已经在提供服务，跳过数据库、依赖安装、迁移与 uvicorn 启动。"
    Write-Info '（这一跳过是有意的：既有实例的库和迁移状态由起它的人负责。'
    Write-Info '  若你刚改了迁移，请先 scripts/dev-down.ps1 再重跑本脚本。）'
} elseif (Test-PortBusy $ApiPort) {
    Stop-WithGuidance "$ApiPort 端口被占用，但它不是 RepoMesh API（/docs 打不开）。" @(
        "前端的开发代理写死打 127.0.0.1:$ApiPort（frontend/vite.config.ts），这个端口不能换。",
        "请先停掉占用 $ApiPort 的进程： netstat -ano | findstr :$ApiPort"
    )
} else {
    # 1a. Postgres
    if ($DsnExplicit) {
        Write-Skip '已显式指定 REPOMESH_DATABASE_URL，不起 compose postgres。'
        Write-Info '迁移与后端都会连这个库，请确认它属于本项目。'
    } else {
        if (-not (Test-Command 'docker')) {
            Stop-WithGuidance '找不到 docker。' @(
                '需要 Docker Desktop（Windows/macOS）或 docker engine（Linux）来起 Postgres。',
                '也可以自带数据库：$env:REPOMESH_DATABASE_URL = "postgresql+asyncpg://user:pw@host:port/db" 之后重跑。'
            )
        }
        docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            Stop-WithGuidance 'Docker 装了但没跑起来。' @('请先启动 Docker Desktop，等它完全就绪后重跑本脚本。')
        }

        $running = @(docker compose ps --services --status running 2>$null)
        if ($running -contains 'postgres') {
            $script:OwnDatabase = $true
            Write-Skip 'compose 的 postgres 容器已在运行。'
        } elseif (Test-PortBusy ([int] $DbPort)) {
            Stop-WithGuidance "宿主 $DbPort 端口已被占用，但占用它的不是本项目 compose 的 postgres 容器。" @(
                '脚本不会对不是自己起的数据库执行迁移——谱系不符的库会被 alembic 改坏。',
                '三选一：',
                '  1) 给本项目的库换端口： $env:REPOMESH_POSTGRES_PORT = "5433"; .\scripts\dev-up.ps1',
                '  2) 复用你已有的库（确认它属于本项目，脚本会对它 alembic upgrade head）：',
                "     `$env:REPOMESH_DATABASE_URL = `"postgresql+asyncpg://repomesh:repomesh@127.0.0.1:$DbPort/repomesh`"",
                "  3) 停掉占用 $DbPort 的服务后重跑"
            )
        } else {
            Write-Info "起 compose postgres（宿主端口 $DbPort）…"
            $env:REPOMESH_POSTGRES_PORT = $DbPort
            docker compose up -d postgres *> $null
            if ($LASTEXITCODE -ne 0) {
                Stop-WithGuidance 'docker compose up -d postgres 失败。' @(
                    '看一眼原因： docker compose up postgres',
                    "常见原因：宿主端口 $DbPort 被占（换 REPOMESH_POSTGRES_PORT）、镜像拉不下来。"
                )
            }
            $script:OwnDatabase = $true
            New-Item -ItemType File -Force -Path (Join-Path $StateDir 'postgres.started') | Out-Null
            Write-Ok 'postgres 容器已启动。'
        }

        if ($script:OwnDatabase) {
            Write-Info '等数据库接受连接…'
            $ready = $false
            foreach ($attempt in 1..30) {
                docker compose exec -T postgres pg_isready -U repomesh -d repomesh *> $null
                if ($LASTEXITCODE -eq 0) { $ready = $true; break }
                Start-Sleep -Seconds 2
            }
            if (-not $ready) {
                Stop-WithGuidance '60 秒内数据库没有就绪。' @(
                    '看容器日志： docker compose logs --tail 50 postgres',
                    '若卷里是旧的、口令不同的数据目录，可以整个项目删掉重来（会清数据）： docker compose down -v'
                )
            }
            Write-Ok '数据库已就绪。'
        }
    }

    # 1b. 依赖
    if (-not (Test-Command 'uv')) {
        Stop-WithGuidance '找不到 uv。' @(
            '安装： powershell -c "irm https://astral.sh/uv/install.ps1 | iex"',
            '或者走全 Docker 方案，宿主只需要 Docker： docker compose --profile console up -d'
        )
    }
    Write-Info '同步 Python 依赖（uv sync --extra dev）…'
    Invoke-Checked 'uv' @('sync', '--extra', 'dev') 'uv sync --extra dev 失败。' @(
        '多半是网络或 Python 版本问题；把上面的报错原文贴出来定位。'
    )
    Write-Ok 'Python 依赖就绪。'

    # 1b-2. 迁移前谱系比对（对齐 dev-up.sh 的 M-9 / A-2 检查）
    # 上面的端口归属守卫只保证“这个库不是别的进程的”，保证不了“这个库的迁移谱系跟
    # 当前代码同源”。一个先于本脚本存在、被采纳的同端口 compose 库，可能停在本地
    # alembic 历史里根本没有的 revision 上——对它 upgrade head 会把它改坏。所以在
    # 迁移前先问一次库的当前 revision：只要 alembic 认不出它（谱系不符），就中止。
    Write-Info '比对数据库迁移谱系（alembic current）…'
    $env:REPOMESH_DATABASE_URL = $Dsn
    $lineageOut = (& uv run alembic current 2>&1 | Out-String)
    if ($lineageOut -match "(?i)can.t locate revision") {
        $stuckRev = if ($lineageOut -match "identified by '([^']+)'") { $Matches[1] } else { $null }
        Stop-WithGuidance "数据库停在本地 alembic 历史里没有的 revision（$(if ($stuckRev) { $stuckRev } else { '见下方原文' })）。" @(
            '这个库的迁移谱系跟当前代码不同源——多半是先于本脚本存在的、别的分支或别的项目',
            '留下的同端口 compose 库。对它执行 upgrade head 会把它改坏，所以这里直接中止。',
            "alembic 原文：$lineageOut",
            '三选一：',
            '  1) 换个空库（换端口）： $env:REPOMESH_POSTGRES_PORT = "5433"; .\scripts\dev-up.ps1',
            '  2) 清掉旧卷重来（会清数据）： docker compose down -v 后重跑本脚本',
            '  3) 若确认这库确属本项目、只是分支超前——先把代码切到与库匹配的分支再起'
        )
    }
    Write-Ok '迁移谱系一致（或是全新空库），可以安全迁移。'

    # 1c. 迁移
    Write-Info "执行数据库迁移（alembic upgrade head → $Dsn）…"
    Invoke-Checked 'uv' @('run', 'alembic', 'upgrade', 'head') 'alembic upgrade head 失败。' @(
        '常见原因：',
        '  * DSN 指向的库不是本项目的库（迁移谱系不符）——换一个空库再试；',
        "  * 库还没起来或口令不对——用 psql 手工连一次 $Dsn 验证；",
        '  * 本地有没提交的迁移冲突——uv run alembic heads 看是不是多头。',
        '脚本不会自动重试，也不会绕过失败继续起服务。'
    )
    Write-Ok '迁移已到 head。'

    # 1d. uvicorn
    Write-Info "启动 API（uvicorn，127.0.0.1:$ApiPort）…"
    $env:REPOMESH_AGENT_ACTION_TOKEN = $ActionToken
    $backend = Start-Process -FilePath (Get-Command 'uv').Source -PassThru -WindowStyle Hidden `
        -WorkingDirectory $RepoRoot `
        -ArgumentList @('run', 'uvicorn', 'repomesh.main:app', '--host', '127.0.0.1', '--port', "$ApiPort") `
        -RedirectStandardOutput (Join-Path $StateDir 'backend.log') `
        -RedirectStandardError (Join-Path $StateDir 'backend.err.log')
    Set-Content -Path (Join-Path $StateDir 'backend.pid') -Value $backend.Id

    $serving = $false
    foreach ($attempt in 1..40) {
        if (Test-HttpServing "http://127.0.0.1:$ApiPort/docs") { $serving = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $serving) {
        Get-Content (Join-Path $StateDir 'backend.err.log') -Tail 30 -ErrorAction SilentlyContinue | Write-Host
        Remove-Item (Join-Path $StateDir 'backend.pid') -ErrorAction SilentlyContinue
        Stop-WithGuidance "80 秒内 API 没有在 $ApiPort 上提供服务（判据是 /docs 可达，不是 /health/ready）。" @(
            "完整日志： $StateDir\backend.log 与 backend.err.log",
            '常见原因：端口被抢、DSN 连不上、依赖装了一半。'
        )
    }
    Write-Ok "API 已就绪： http://127.0.0.1:$ApiPort/docs"
}

# ---------------------------------------------------------------- 2. 种子
Write-Step '[2/4] 演示数据'
if (-not $Seed) {
    Write-Skip '未加 -Seed，不灌演示数据（新库首屏会是空态，属正常）。'
} elseif (-not $script:OwnDatabase -and -not $DsnExplicit) {
    if ($backendSkipped) {
        Write-Note '跳过灌种子：后端是既有实例，脚本无从确认它连的是哪个库，不敢往默认 DSN 写。'
    } else {
        Write-Note '跳过灌种子：本次运行没有起数据库，脚本不往不是自己起的库写数据。'
    }
    Write-Info '确认之后显式指定同一个库再灌：'
    Write-Info '  uv run python scripts/seed-console-demo.py --database-url <后端在用的 DSN>'
} else {
    Write-Info "灌演示种子（目标库 $Dsn）…"
    uv run python scripts/seed-console-demo.py --database-url $Dsn
    if ($LASTEXITCODE -eq 0) {
        Write-Ok '演示数据已写入。'
    } else {
        Write-Note '种子脚本失败——控制台仍可用，只是首屏是空态。'
        Write-Info '半写状态无法靠重跑修复（脚本自己会报 SeedIncomplete）：换一个空库重来最省事。'
    }
}

# ---------------------------------------------------------------- 3. 前端
Write-Step "[3/4] 前端开发服务器（127.0.0.1:$WebPort）"
if (Test-HttpServingSettled "$ConsoleUrl/") {
    Write-Skip "$WebPort 已经在提供服务，跳过 npm install 与 vite 启动。"
} elseif (Test-PortBusy $WebPort) {
    Stop-WithGuidance "$WebPort 端口被占用，但打不开页面。" @(
        "vite 配了 strictPort，端口不能自动顺延，也不能换（README 里的地址写死 $WebPort）。",
        "请先停掉占用它的进程： netstat -ano | findstr :$WebPort"
    )
} else {
    $npmCommand = Get-Command 'npm.cmd' -ErrorAction SilentlyContinue
    if (-not $npmCommand) {
        $npmCommand = Get-Command 'npm' -ErrorAction SilentlyContinue
    }
    if (-not $npmCommand) {
        Stop-WithGuidance '找不到 npm。' @(
            '装 Node.js 20+： https://nodejs.org/',
            '或者走全 Docker 方案： docker compose --profile console up -d'
        )
    }
    $frontendDir = Join-Path $RepoRoot 'frontend'
    $npm = $npmCommand.Source
    if (Test-Path (Join-Path $frontendDir 'node_modules')) {
        Write-Skip 'frontend/node_modules 已存在，跳过 npm install（要更新依赖请手工 npm install）。'
    } else {
        Write-Info '安装前端依赖（npm install）…'
        Push-Location $frontendDir
        try {
            & $npm install
            if ($LASTEXITCODE -ne 0) {
                Stop-WithGuidance 'npm install 失败。' @('把上面的报错原文贴出来定位；国内网络常见是 registry 超时。')
            }
        } finally {
            Pop-Location
        }
        Write-Ok '前端依赖就绪。'
    }

    Write-Info '启动 vite…'
    # vite.config.ts 的代理默认已指向容器全执行面 API（:8000）。本脚本起的是
    # 计划态后端（:8100），所以在这里把 REPOMESH_API_TARGET 显式指回自己的
    # :8100——否则计划态控制台会打到可能没起的 :8000 上（对齐 dev-up.sh 第 300-303 行）。
    $env:REPOMESH_API_TARGET = "http://127.0.0.1:$ApiPort"
    $frontend = Start-Process -FilePath $npm -PassThru -WindowStyle Hidden `
        -WorkingDirectory $frontendDir -ArgumentList @('run', 'dev') `
        -RedirectStandardOutput (Join-Path $StateDir 'frontend.log') `
        -RedirectStandardError (Join-Path $StateDir 'frontend.err.log')
    Set-Content -Path (Join-Path $StateDir 'frontend.pid') -Value $frontend.Id

    $serving = $false
    foreach ($attempt in 1..30) {
        if (Test-HttpServing "$ConsoleUrl/") { $serving = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $serving) {
        Get-Content (Join-Path $StateDir 'frontend.err.log') -Tail 30 -ErrorAction SilentlyContinue | Write-Host
        Remove-Item (Join-Path $StateDir 'frontend.pid') -ErrorAction SilentlyContinue
        Stop-WithGuidance "60 秒内前端没有在 $WebPort 上提供服务。" @("完整日志： $StateDir\frontend.log 与 frontend.err.log")
    }
    Write-Ok '前端已就绪。'
}

# ---------------------------------------------------------------- 4. 打开
Write-Step '[4/4] 打开控制台'
if ($NoBrowser) {
    Write-Skip '加了 -NoBrowser，不自动打开。'
} else {
    try {
        Start-Process $ConsoleUrl | Out-Null
        Write-Ok "已尝试用默认浏览器打开 $ConsoleUrl"
    } catch {
        Write-Note '没能自动打开浏览器，手工访问下面的地址即可。'
    }
}

Write-Host @"

------------------------------------------------------------------
控制台： $ConsoleUrl
接口文档： http://127.0.0.1:$ApiPort/docs

身份：    控制台有登录门；首次访问请点『首次部署？初始化管理员』设置本地
          管理员账号（用户名、显示名、密码——密码至少 12 位）。
数据源：  默认就是真实数据（打 8100 后端）；演示夹具要显式加 ?source=replay。

执行面：  本脚本只起“计划态”控制台（后端 API + 前端 + 数据库），不含 AgentTeams
          执行面。所以 materialize / 派单 / 真正跑 agent 会返回 503
          （“…has no rooms for this project's teams (AgentTeams request failed…)”）——
          这是预期限制、不是故障：宿主进程本就连不到控制器（控制器的 8090/6167 只在
          agentteams-net 内网可达，没有映射到宿主）。要完整执行面（能 materialize、
          能派单、能跑 agent），用容器内跑、接入 agentteams-net 的那条路：
            scripts\start-platform.ps1          # 首次装执行面加 -InstallAgentTeams
          它把 API 跑在容器里并连上控制器（就是已在跑的 :8000 那套）。

日志： $StateDir
收摊： .\scripts\dev-down.ps1（只停本脚本起的组件，且逐项问你）
------------------------------------------------------------------
"@

if ($script:Warnings.Count -gt 0) {
    Write-Host "`n有 $($script:Warnings.Count) 条注意事项："
    foreach ($line in $script:Warnings) { Write-Host "  * $line" }
}
