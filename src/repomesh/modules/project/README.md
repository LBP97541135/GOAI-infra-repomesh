# Project

Owns project lifecycle, participating repositories, memberships, repository classifications,
workstreams, and the final confirmed project scope. It does not own engineering-spec content,
task execution, or repository scanning.

## Project Agent topology

Long-lived Agent identities remain in Agent Directory. A project stores only temporary membership:

```text
Organization Leader (AgentTeams Manager)
  -> Repository Team A: Repository Leader + selected Workers
  -> Repository Team B: Repository Leader + selected Workers
```

Each participating repository has one long-lived AgentTeams Team and exactly one Repository Leader.
Projects reference that Team instead of creating duplicate runtime Teams. An Agent cannot join two
repository Teams in the same project. The automatic creation API accepts only the organization,
project and repository IDs, then resolves the active Organization Leader, Repository Leader and
Workers from Agent Directory. Creation validates the durable hierarchy before storing the topology;
reconciliation creates a missing runtime Team or reuses the existing repository Team.

PostgreSQL tables `project.agent_topologies` and `project.repository_agent_teams` are the source of
truth. The stable `repomesh-team-{repository_id}` name is the runtime binding shared across projects;
Matrix room IDs are cached bindings, not business identity.

## Decomposition mode

Each repository team records who breaks its repository-level task into worker tasks:
`decomposition_mode` is `server` (the platform decomposes and dispatches in the same step) or
`leader` (the batch stops after the leader task and waits for that team's external Repository
Leader to submit a plan). `server` is the default and the resting state.

Only reconciliation sets `leader`, and only as a consequence of adoption: the controller's worker
document for the repository leader — the same read that finds which AgentTeams Team this repository
already uses — reports `containerManaged`, and a leader the controller does not containerize is one
a Bridge is serving. There is no script, console action or admin route that sets the mode
(adjudication D-2). The promotion is one-way: a later pass that observes no external leader, because
the controller was unreachable or answered without the field, leaves an adopted team adopted.

`TeamDecompositionModeReader` is the narrow contract task orchestration reads it through, answered
from the persisted row rather than from the controller; every absence — no topology, no team, no
adoption — is `server`.
