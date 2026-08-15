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
truth. The stable `rm-team-{repository_id}` name is the runtime binding shared across projects;
Matrix room IDs are cached bindings, not business identity.
