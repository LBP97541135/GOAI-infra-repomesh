param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9][a-z0-9-]+$')]
    [string]$RepositoryKey,
    [string]$Model = "claude-sonnet-4-6",
    [ValidateSet("copaw", "openclaw", "hermes", "openhuman")]
    [string]$Runtime = "copaw",
    [ValidateRange(1, 8)]
    [int]$WorkerCount = 1,
    [string]$ManagerContainer = "agentteams-manager"
)

$ErrorActionPreference = "Stop"

function Invoke-Agt {
    param([string[]]$AgtArguments)
    & docker exec $ManagerContainer agt @AgtArguments
    if ($LASTEXITCODE -ne 0) {
        throw "AgentTeams command failed: agt $($AgtArguments -join ' ')"
    }
}

function Get-AgentTeamsResources([string]$Kind) {
    $raw = Invoke-Agt -AgtArguments @("get", $Kind, "-o", "json")
    return ($raw | ConvertFrom-Json)
}

$leaderName = "repomesh-$RepositoryKey-leader"
$teamName = "repomesh-$RepositoryKey-team"
$workerNames = 1..$WorkerCount | ForEach-Object {
    "repomesh-$RepositoryKey-worker-{0:d2}" -f $_
}

$organizationSoul = @"
You are the RepoMesh Organization Leader. Every software change must start as a RepoMesh Project.
Delegate repository scope to Repository Leaders. Never edit repository source, run repository
tests, stage files, or create Git commits. Missing runtime resources are blockers, not permission
to code. Completion requires Project, Specification, Task, Runner, test, and commit evidence.

For CoPaw delegation, resolve the Repository Leader with the agt worker listing and send the
assignment to that Leader's own roomID, not the Team leaderDMRoomID. Use copaw channels send with
--target-session equal to that roomID and begin --text with the Leader's full Matrix ID.
Do not claim delivery from generated narration. Require a successful send result before changing
the task to assigned or waiting for the Leader; otherwise leave it blocked and report the failure.
Repository-team task files must be copied to the team's MinIO namespace at
agentteams/agentteams-storage/teams/repomesh-$RepositoryKey-team/shared/tasks/TASK_ID/. The global
shared/tasks namespace alone is insufficient because Team Worker file-sync reads the team prefix.
Verify the team-prefixed objects exist before notifying the Repository Leader.
When a Repository Leader reports completion in a Leader or Team Room, send the final Project,
Specification, Task, test, and commit evidence to the Admin DM with copaw channels send. A reply in
the Leader Room is not user delivery. Do not close the project until the Admin DM send succeeds.
"@.Trim()
Invoke-Agt -AgtArguments @(
    "update", "manager", "--name", "default", "--soul", $organizationSoul
) | Out-Null

# Updating the Manager restarts its container. Wait until docker exec is usable before issuing
# subsequent AgentTeams control-plane commands.
$managerDeadline = (Get-Date).AddMinutes(2)
do {
    Start-Sleep -Seconds 2
    & docker exec $ManagerContainer agt get managers -o json *> $null
    $managerReady = $LASTEXITCODE -eq 0
} while (-not $managerReady -and (Get-Date) -lt $managerDeadline)
if (-not $managerReady) {
    throw "Manager did not become ready after SOUL update: $ManagerContainer"
}

$existingWorkers = @(Get-AgentTeamsResources workers).workers
$leaderSoul = @"
You are the Repository Leader for $RepositoryKey. Write repository Engineering Specs, decompose
approved work into structured RepoMesh Tasks, coordinate Workers, and review their evidence.
Never modify production code, start a coding CLI directly, or create Git commits.

For CoPaw delegation, resolve the Coding Worker with the agt worker listing and send the task to
that Worker's own roomID. Use copaw channels send with --target-session equal to that roomID and
begin --text with the Worker's full Matrix ID. Do not claim delivery unless the send command
returned success. Include the repository URL, branch, allowed paths, acceptance command,
commit/push requirement, and Engineering Spec in the assignment.
Read assigned task files from the team-synced shared/tasks directory. If they are missing, report a
storage-namespace blocker rather than inventing the Specification from chat history.
"@.Trim()
if ($leaderName -notin $existingWorkers.name) {
    Invoke-Agt -AgtArguments @(
        "create", "worker", "--name", $leaderName, "--runtime", $Runtime,
        "--model", $Model, "--identity", "Repository Leader for $RepositoryKey",
        "--soul", $leaderSoul, "--skills", "git-delegation", "--no-wait", "-o", "json"
    ) | Out-Null
}
Invoke-Agt -AgtArguments @("update", "worker", "--name", $leaderName, "--soul", $leaderSoul) |
    Out-Null

foreach ($workerName in $workerNames) {
    $workerSoul = @"
You are a Coding Worker for $RepositoryKey. Execute only an assigned RepoMesh Task backed by an
approved Task Spec and visibility bundle. Use the RepoMesh Runner and selected coding-agent
adapter. Do not change project scope, contracts, or acceptance criteria. Report blockers and
results to your Repository Leader.
"@.Trim()
    if ($workerName -notin $existingWorkers.name) {
        Invoke-Agt -AgtArguments @(
            "create", "worker", "--name", $workerName, "--runtime", $Runtime,
            "--model", $Model, "--identity", "Coding Worker for $RepositoryKey",
            "--soul", $workerSoul, "--skills", "git-delegation", "--no-wait", "-o", "json"
        ) | Out-Null
    }
    Invoke-Agt -AgtArguments @("update", "worker", "--name", $workerName, "--soul", $workerSoul) |
        Out-Null
}

$expected = @($leaderName) + @($workerNames)
$deadline = (Get-Date).AddMinutes(3)
do {
    $workers = @(Get-AgentTeamsResources workers).workers
    $pending = @($expected | Where-Object {
        $name = $_
        -not ($workers | Where-Object { $_.name -eq $name -and $_.phase -eq "Running" })
    })
    if ($pending.Count -eq 0) { break }
    Start-Sleep -Seconds 5
} while ((Get-Date) -lt $deadline)
if ($pending.Count -gt 0) {
    throw "Workers did not become Running: $($pending -join ', ')"
}

$teams = @(Get-AgentTeamsResources teams).teams
if ($teamName -notin $teams.name) {
    Invoke-Agt -AgtArguments @(
        "create", "team", "--name", $teamName, "--leader-name", $leaderName,
        "--workers", ($workerNames -join ','), "--peer-mentions=false", "--description",
        "RepoMesh repository team for $RepositoryKey. Coding requires a RepoMesh Task and Runner."
    ) | Out-Null
}

$deadline = (Get-Date).AddMinutes(2)
do {
    $team = @((Get-AgentTeamsResources teams).teams | Where-Object name -eq $teamName)
    if ($team.Count -eq 1 -and $team[0].phase -eq "Active") { break }
    Start-Sleep -Seconds 5
} while ((Get-Date) -lt $deadline)
if ($team.Count -ne 1 -or $team[0].phase -ne "Active") {
    throw "Team did not become Active: $teamName"
}

[pscustomobject]@{
    organizationLeader = "default"
    repositoryLeader = $leaderName
    workers = $workerNames
    team = $teamName
    phase = $team[0].phase
    teamRoomID = $team[0].teamRoomID
    leaderDMRoomID = $team[0].leaderDMRoomID
} | ConvertTo-Json -Depth 5
