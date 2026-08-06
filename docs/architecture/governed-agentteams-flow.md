# RepoMesh Governed AgentTeams Flow

This flow is mandatory for software-change requests. AgentTeams owns runtime identities and rooms;
RepoMesh owns the durable project, specification, task, authorization, run, and evidence records.

## Roles

| Role | May read | May write | Must not do |
| --- | --- | --- | --- |
| Organization Leader | requirement, repository summaries, project progress | project, repository scope, workstream assignment | edit code, run repository tests, commit |
| Repository Leader | project scope, repository summary, contracts, Worker evidence | Engineering Spec, Task Spec, task DAG, review result | edit production code, invoke coding CLI, commit |
| Worker | assigned Task Spec, approved context bundle, permitted repository paths | isolated Runner workspace and task result | change scope/spec, access other repositories, commit outside Runner |

The backend rejects coding execution for every identity except `worker`. Agent prompts are guidance;
the role check, immutable context bundle, path policy, isolated workspace, and Runner commit policy are
the enforcement boundaries.

## Required State Machine

1. **Intake**: create a RepoMesh Project from the original requirement.
2. **Scope**: Organization Leader proposes repository scope from repository summaries; affected
   Repository Leaders review it; the Organization Leader confirms it.
3. **Team projection**: reconcile one AgentTeams Team per affected repository. A Team contains one
   Repository Leader and one or more Workers. Worker peer mentions remain disabled.
4. **Specification**: Repository Leader writes and submits the repository Engineering Spec. Contract
   ownership, allowed paths, acceptance criteria, and test commands must be explicit before approval.
5. **Task planning**: Repository Leader creates the repository task DAG. Every coding node has one
   Worker assignee, one approved Task Spec, dependencies, allowed paths, and acceptance commands.
6. **Execution**: Worker calls `start_assigned_task`. RepoMesh verifies Worker role, assignment,
   approved context, repository membership, capabilities, and workspace before dispatching Runner.
7. **Evidence**: Runner invokes the coding-agent adapter, records events, enforces path/tool policy,
   runs Task tests, and creates the commit only after validation succeeds.
8. **Review**: Repository Leader reviews diff and Task evidence, then records repository integration
   results. It never edits the candidate itself.
9. **Project validation**: Organization Leader coordinates cross-repository integration and regression
   gates. Failed gates create new tasks; they do not cause direct Manager edits.
10. **Delivery**: RepoMesh prepares the ChangeSet and repository PRs from accepted commits.

## Fail-Closed Rules

- No AgentTeams Worker or Team: project is blocked; the Manager must provision/reconcile resources.
- No confirmed repository scope: no Engineering Spec may be approved.
- No approved Task Spec: no Runner task may be created.
- Caller is not a Worker or is not the assignee: `start_assigned_task` is rejected.
- Contract or acceptance change discovered during execution: Worker reports a blocker; Repository
  Leader escalates it to the Organization Leader for impact assessment and re-planning.
- Test or path-policy failure: Runner creates no commit and preserves the workspace for diagnosis.
- RepoMesh is unavailable: agents report a blocker; they must not fall back to direct repository work.

## AgentTeams Delivery Invariant

An assignment is not delivered merely because an agent generated a status sentence. With CoPaw,
the sender resolves the assignee's own `roomID`, invokes `copaw channels send`, starts the text with
the assignee's full Matrix ID, and requires a successful command result. The Team
`leaderDMRoomID` is not a substitute for the Manager-visible Repository Leader Worker Room. Until
delivery succeeds, the task stays blocked rather than moving to an assigned or waiting state.

Team assignments also have a storage invariant: files consumed by a Repository Leader or Worker
must exist under `teams/<team>/shared/tasks/<task-id>/` in AgentTeams storage before the Matrix
notification is sent. The Manager-global `shared/tasks/` prefix is not visible through a Team
Worker's file-sync path and cannot be used as the only copy.

Completion delivery is also cross-room: a Repository Leader reports in its authorized coordination
room, but the Organization Leader must forward the accepted Project, Task, test, and commit evidence
to the Admin DM. A completion acknowledgement in the Leader Room does not complete user delivery.

## Provisioning

Provision or reconcile a repository team with:

```powershell
pwsh ./scripts/provision-repomesh-team.ps1 -RepositoryKey pricing -WorkerCount 1
```

The command is safe to rerun. It reuses the existing Organization Leader, creates missing repository
runtime identities, waits for Workers, creates the Team, and returns only non-secret runtime IDs.
# Task publication boundary

For Worker tasks, RepoMesh is the source of truth and AgentTeams Team storage is a materialized
execution view. `AgentTeamsTaskPublisher` writes `teams/{team}/shared/tasks/{task}/spec.md`,
`meta.json`, and `manifest.json` through an atomic file replacement, then reads the files back and
verifies their SHA-256 digest. Only a verified publication may be announced in Matrix. Configure
`REPOMESH_AGENTTEAMS_STORAGE_ROOT` to the host directory mirrored by AgentTeams storage; the
Compose `api` service mounts that directory at `/agentteams-storage`.

The Worker notification contains only the immutable task reference and an MCP call to
`repomesh-task-control.start_assigned_task`. The MCP service validates assignment and Worker role,
builds the approved coding package, creates an isolated worktree and context bundle, and dispatches
the configured coding-agent adapter through RepoMesh Runner.
