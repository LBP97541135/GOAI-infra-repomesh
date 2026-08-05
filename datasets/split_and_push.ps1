param(
    [string]$SourceRepo = "c:\Users\PC\.trae\work\6a7022c7c017ef30afc6ec04\train-ticket-full",
    [string]$Org = "repomesh-train-ticket",
    [string]$Token = "",  # 从环境变量 GITHUB_TOKEN 读取，或运行时传入
    [string]$WorkDir = "c:\Users\PC\.trae\work\6a7022c7c017ef30afc6ec04\split-output"
)

# 如果没传 Token，尝试从环境变量读取
if (!$Token) {
    $Token = $env:GITHUB_TOKEN
}
if (!$Token) {
    Write-Host "Error: 请通过 -Token 参数或 GITHUB_TOKEN 环境变量提供 GitHub token" -ForegroundColor Red
    Write-Host "用法: .\split_and_push.ps1 -Token 'github_pat_xxx'"
    exit 1
}

$ErrorActionPreference = "Continue"
$headers = @{ "Authorization" = "Bearer $Token"; "Accept" = "application/vnd.github+json" }

# 获取 GitHub 上已有的仓库列表（跳过这些）
Write-Host "Fetching existing repos from GitHub..."
$existingRepos = @()
$page = 1
do {
    try {
        $repos = Invoke-RestMethod -Uri "https://api.github.com/orgs/$Org/repos?per_page=100&page=$page" -Headers $headers -TimeoutSec 10
        foreach ($r in $repos) { $existingRepos += $r.name }
        $page++
    } catch { break }
} while ($repos.Count -gt 0)
Write-Host "Already on GitHub: $($existingRepos.Count) repos"

# 获取所有 ts-* 子目录
$services = @()
foreach ($d in Get-ChildItem -Path $SourceRepo -Directory) {
    $n = $d.Name
    if ($n -and $n.StartsWith('ts-') -and $n -ne 'ts-common' -and $n -ne 'ts-ui-dashboard') {
        $services = $services + @($n)
    }
}

# 过滤掉已存在的
$todo = @()
foreach ($s in $services) {
    if ($existingRepos -notcontains $s) {
        $todo = $todo + @($s)
    }
}
Write-Host "To do: $($todo.Count) services (skipping $($existingRepos.Count) already done)" -ForegroundColor Cyan

if ($todo.Count -eq 0) {
    Write-Host "All done!" -ForegroundColor Green
    exit 0
}

if (!(Test-Path $WorkDir)) {
    New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
}

$success = @()
$failed = @()

foreach ($svc in $todo) {
    $timeStart = Get-Date
    Write-Host "`n[$svc] Processing..." -ForegroundColor Yellow

    $svcDir = Join-Path $WorkDir $svc
    if (Test-Path $svcDir) { Remove-Item -Recurse -Force $svcDir }

    # 1. Clone
    Write-Host "  Cloning..."
    git clone --no-hardlinks $SourceRepo $svcDir 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "  FAILED: clone" -ForegroundColor Red; $failed += $svc; continue }

    # 2. Filter-branch
    Write-Host "  Filter-branch..."
    Push-Location $svcDir
    git filter-branch --prune-empty --subdirectory-filter $svc -- --all 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "  FAILED: filter-branch" -ForegroundColor Red; Pop-Location; $failed += $svc; continue }

    if (Test-Path ".git/refs/original") { Remove-Item -Recurse -Force ".git/refs/original" }
    git reflog expire --expire=now --all 2>&1 | Out-Null
    git gc --prune=now 2>&1 | Out-Null

    # 3. 创建 GitHub 仓库（public）
    Write-Host "  Creating GitHub repo..."
    $createBody = @{ name = $svc; private = $false; visibility = "public"; description = "Train-Ticket microservice: $svc" } | ConvertTo-Json
    try {
        $repo = Invoke-RestMethod -Uri "https://api.github.com/orgs/$Org/repos" -Method Post -Headers $headers -Body $createBody -ContentType "application/json" -TimeoutSec 15
        Write-Host "  Created: $($repo.full_name)" -ForegroundColor Green
    } catch {
        if ([int]$_.Exception.Response.StatusCode -eq 422) {
            Write-Host "  Repo exists, continuing..." -ForegroundColor DarkYellow
        } else {
            Write-Host "  FAILED: create - $($_.Exception.Message)" -ForegroundColor Red; Pop-Location; $failed += $svc; continue
        }
    }

    # 4. Push（带重试）
    Write-Host "  Pushing..."
    $remoteUrl = "https://github.com/$Org/$svc.git"
    git remote remove origin 2>&1 | Out-Null
    $authUrl = $remoteUrl -replace "https://", "https://x-access-token:$Token@"
    git remote add origin $authUrl
    git branch -m master main 2>&1 | Out-Null

    $pushed = $false
    for ($retry = 0; $retry -lt 3; $retry++) {
        git push -u origin --all 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
        Write-Host "  Retry $($retry+1)/3..." -ForegroundColor DarkYellow
        Start-Sleep -Seconds 5
    }

    if (!$pushed) {
        Write-Host "  FAILED: push after 3 retries" -ForegroundColor Red; Pop-Location; $failed += $svc; continue
    }

    $elapsed = ((Get-Date) - $timeStart).TotalSeconds
    Pop-Location
    Write-Host ("  DONE: {0} ({1:F0}s)" -f $svc, $elapsed) -ForegroundColor Green
    $success += $svc
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "This run: $($success.Count) success, $($failed.Count) failed"
Write-Host "Total: $($existingRepos.Count + $success.Count) / $($services.Count) on GitHub"
if ($failed.Count -gt 0) { Write-Host "Failed:" -ForegroundColor Red; foreach ($f in $failed) { Write-Host "  - $f" } }
Write-Host "========================================" -ForegroundColor Cyan
