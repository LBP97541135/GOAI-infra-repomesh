# AgentTeams Integration

## Pinned Upstream Contract

RepoMesh targets
[`agentscope-ai/AgentTeams` v1.2.0](https://github.com/agentscope-ai/AgentTeams/tree/2ea027403398dfa06f3fc86445042d59f4684d71)
at commit `2ea027403398dfa06f3fc86445042d59f4684d71` under Apache-2.0.

This pin matters because v1.2 finalized the Team and Worker contract: Workers are independent
resources and a Team references them through `workerMembers`. RepoMesh must not send the former
inline Worker shape.

## Responsibility Boundary

AgentTeams and Coding Agent adapters are different layers:

| Layer | Owns |
| --- | --- |
| RepoMesh | projects, repository scope, specifications, tasks, evidence, validation, delivery |
| AgentTeams | Manager/Worker/Team runtime projections, rooms, collaboration and human intervention |
| Coding Agent Adapter | Claude Code, Codex, Cursor and other CLI-specific launch/session behavior |

AgentTeams messages and resources are runtime projections. RepoMesh IDs and database records
remain the source of truth.

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
| Repository manager or team coordinator | Worker with role `team_leader` |
| Task executor | Worker with role `worker` |
| Worker role instructions | `identity`, `soul`, `agents` |
| Approved built-in capabilities | `skills` names |
| Collaboration channel | Team `teamRoomID` |
| Task dispatch | Matrix `m.room.message` |

Resource names use `rm-{kind}-{full UUID hex}` so retries map to the same external identity.

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

## Current Completion Boundary

Implemented and contract-tested:

- fixed upstream version and resource mapping;
- typed Manager, Worker and Team projections;
- health/version, ensure Manager/Worker/Team and Worker lifecycle calls;
- bearer authentication and external error mapping;
- idempotent Matrix task delivery.

Still required for an end-to-end demo:

- start an AgentTeams v1.2 stack and run the live compatibility test;
- persist RepoMesh-to-AgentTeams resource bindings;
- build the application service that projects a confirmed RepoMesh project into Workers/Team;
- route Matrix events back as observations and feedback, without treating chat as business state;
- connect an AgentTeams Worker assignment to the Coding Agent Runtime launch plan.
