# AgentTeams Integration

## Pinned Upstream Contract

RepoMesh targets
[`agentscope-ai/AgentTeams` v1.2.0](https://github.com/agentscope-ai/AgentTeams/tree/793db242257a569d911b1aa59c1cd554af78511f)
at commit `793db242257a569d911b1aa59c1cd554af78511f` under Apache-2.0.

This pin matters because v1.2 finalized the Team and Worker contract: Workers are independent
resources and a Team references them through `workerMembers`. RepoMesh must not send the former
inline Worker shape.

## Responsibility Boundary

AgentTeams and Coding Agent adapters are different layers:

| Layer | Owns |
| --- | --- |
| RepoMesh | projects, repository scope, specifications, tasks, evidence, validation, delivery |
| AgentTeams | Manager/Worker/Team runtime projections, rooms, collaboration and human intervention |
| RepoMesh Runner | coding CLI process, native session, tests, execution artifacts and runtime events |
| Coding Agent Adapter | Claude Code, Codex, Cursor and other CLI-specific launch/session behavior |

AgentTeams Manager/Worker resources are the runtime identity and configuration source of truth.
RepoMesh records remain the source of truth only for business identity, repository responsibility,
project membership, authorization and delivery state.

## Two API Paths

### Controller API

Default upstream address: `http://localhost:8090`.

| Operation | Upstream endpoint |
| --- | --- |
| Health | `GET /healthz` |
| Version | `GET /api/v1/version` |
| Create/get Manager | `POST /api/v1/managers`, `GET /api/v1/managers/{name}` |
| Create/get Worker | `POST /api/v1/workers`, `GET /api/v1/workers/{name}` |
| Ensure Worker ready | `POST /api/v1/workers/{name}/ensure-ready` |
| Create/get Team | `POST /api/v1/teams`, `GET /api/v1/teams/{name}` |

### Matrix Client API

Tasks are messages, not Controller resources:

```text
PUT /_matrix/client/v3/rooms/{roomId}/send/m.room.message/{transactionId}
```

RepoMesh uses the run attempt's stable idempotency key as `transactionId`. Retrying the same
delivery therefore does not create another Matrix event.

## Resource Mapping

| RepoMesh concept | AgentTeams projection |
| --- | --- |
| Project execution group | Team |
| Repository Leader | Worker with role `team_leader` |
| Task executor | Worker with role `worker` |
| Worker role instructions | `identity`, `soul`, `agents` |
| Approved built-in capabilities | `skills` names |
| Collaboration channel | Team `teamRoomID` |
| Task dispatch | Matrix `m.room.message` |

Resource names use `rm-{kind}-{full UUID hex}` so retries map to the same external identity.

## Agent registration and binding

AgentTeams creates and configures native Manager/Worker resources. RepoMesh Agent Directory then
registers a minimal `AgentPrincipal` and a unique resource binding. It does not copy model,
runtime, identity, Soul, Skills, MCP servers, channel policy or Matrix state.

```text
AgentTeams Manager / Worker
            |
            +-> RepoMesh AgentPrincipal binding
                    +-> organization and Leader chain
                    +-> repository and responsibility paths
                    +-> enabled/disabled business status
```

The Organization Leader binds to an AgentTeams Manager. Repository Leaders and Workers bind to
existing AgentTeams Workers and are attached to project Teams by native resource name. Project
reconciliation never recreates their runtime configuration.

AgentTeams channel policies control communication reachability; Context visibility remains a
RepoMesh permission decision derived from role, project membership and Task/Run delegation, then
enforced when immutable run bundles are built. Neither Matrix room membership nor prompt text
grants access to RepoMesh context.

## Idempotency And Reconciliation

The AgentTeams Controller does not currently document native idempotency-key handling for
resource creation. RepoMesh therefore implements create as:

1. `GET` the deterministic resource name.
2. Reuse it only when its observable projection matches.
3. Otherwise `POST` the resource with `Idempotency-Key` for tracing.
4. If creation returns `409`, read it again and reconcile the projection.
5. Raise `AgentTeamsConflict` when an existing resource differs; never silently overwrite it.

Worker `ensure-ready` and `sleep` are convergent lifecycle operations. Matrix task delivery is
idempotent through the transaction id.

## Runtime Configuration

The composition root always creates the Controller client. It creates the Matrix messenger only
when REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN is configured; an absent token means task dispatch is
unavailable rather than anonymously attempted.

REPOMESH_AGENTTEAMS_REQUIRED defaults to false for isolated RepoMesh development. The full-platform
Compose profile sets it to true, so readiness requires both PostgreSQL and the AgentTeams
Controller. Liveness remains process-only.

## Current Completion Boundary

Implemented and contract-tested:

- fixed upstream version and resource mapping;
- typed Manager, Worker and Team projections;
- health/version, ensure Manager/Worker/Team and Worker lifecycle calls;
- bearer authentication and external error mapping;
- idempotent Matrix task delivery;
- composition-root wiring, client shutdown, and optional/required readiness modes;
- minimal Agent principals with strict Leader hierarchy and native resource bindings;
- role-ceiling visibility and tool-policy evaluation with explicit denies;
- idempotent Agent principal registration and direct Team references to native Workers;
- confirmed RepoMesh project projection into AgentTeams Teams;
- Worker assignment publication through Team-scoped storage plus Matrix notification;
- `repomesh-task-control.start_assigned_task` as the Worker MCP execution entry;
- RepoMesh Runner dispatch, task-scoped test execution, commit creation and result write-back.

Live validation evidence is recorded in
`docs/test-results/live-github-delivery-e2e-20260810.md`: user intake, project planning,
Repository Leader tasking, Worker MCP start, coding-agent execution, tests, commits, Draft PRs,
CI/review observations, governance readiness and dependency-ordered merge all completed.

Remaining hardening work:

- persist observed AgentTeams resource generations for drift auditing;
- route Matrix inbound feedback into structured collaboration observations without treating chat as
  business state;
- add a runtime-neutral AgentTeams `toolPolicy` contract for hard builtin-tool enforcement;
- move static MCP gateway token lists to dynamic Worker key registration, rotation and revocation.
