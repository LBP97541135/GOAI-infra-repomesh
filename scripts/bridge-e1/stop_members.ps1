<#
.SYNOPSIS
    Stop the Bridge processes start_members.ps1 recorded, by PID.

.DESCRIPTION
    By PID file, because that is the only record of which process serves which
    member -- and `pkill -f` from Git Bash does not kill a process launched this
    way at all. Each PID is confirmed against its own command line before it is
    killed: PIDs are reused, and a stale file naming a recycled id would
    otherwise point Stop-Process at an unrelated process.

    -Sweep additionally lists (and stops) any repomesh_agent_bridge run process
    this machine has that no PID file claims -- the fallback for a session whose
    PID files were lost. That query is Get-CimInstance Win32_Process matched on
    CommandLine, which is the only thing on Windows that sees the full argument
    list.

.EXAMPLE
    ./stop_members.ps1 -Members members.json -PidDir out\pids -Subset m7
    ./stop_members.ps1 -Members members.json -PidDir out\pids -Sweep
#>
param(
    [Parameter(Mandatory = $true)][string]$Members,
    [Parameter(Mandatory = $true)][string]$PidDir,
    [string]$Subset,
    [switch]$Sweep
)

$ErrorActionPreference = "Stop"

$roster = Get-Content -Raw -LiteralPath $Members | ConvertFrom-Json
$selected = $roster.members
if ($Subset) {
    $selected = @($roster.members | Where-Object { $_.subsets -contains $Subset })
    if ($selected.Count -eq 0) {
        throw "no roster member is tagged '$Subset'"
    }
}

$stopped = @()

foreach ($member in $selected) {
    $pidFile = Join-Path $PidDir ("{0}.pid" -f $member.key)
    if (-not (Test-Path -LiteralPath $pidFile)) {
        Write-Host ("no pid    {0,-14} {1} does not exist" -f $member.key, $pidFile)
        continue
    }
    $recorded = [int](Get-Content -LiteralPath $pidFile | Select-Object -First 1)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$recorded" -ErrorAction SilentlyContinue
    if (-not $process) {
        Write-Host ("gone     {0,-14} pid={1} already exited" -f $member.key, $recorded)
        Remove-Item -LiteralPath $pidFile
        continue
    }
    if ($process.CommandLine -notlike "*repomesh_agent_bridge*") {
        throw "pid $recorded is not a Bridge process ($($process.Name)); $pidFile is stale, delete it by hand"
    }
    Stop-Process -Id $recorded -Force
    Remove-Item -LiteralPath $pidFile
    $stopped += $recorded
    Write-Host ("stopped  {0,-14} pid={1}" -f $member.key, $recorded)
}

if ($Sweep) {
    $strays = @(Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -like "*repomesh_agent_bridge*run*" -and $stopped -notcontains $_.ProcessId })
    if ($strays.Count -eq 0) {
        Write-Host "sweep: no unclaimed repomesh_agent_bridge process"
    }
    foreach ($stray in $strays) {
        Write-Host ("sweep    pid={0} {1}" -f $stray.ProcessId, $stray.CommandLine)
        Stop-Process -Id $stray.ProcessId -Force
    }
}
