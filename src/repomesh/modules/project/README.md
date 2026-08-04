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

Each participating repository has exactly one project Team and exactly one Repository Leader. An
Agent cannot join two repository Teams in the same project. Creation validates organization,
repository, role and durable Leader relationships before storing the topology. Reconciliation then
ensures the Manager and Worker resources before creating one AgentTeams Team per repository.

PostgreSQL tables `project.agent_topologies` and `project.repository_agent_teams` are the source of
truth. AgentTeams Team names and Matrix room IDs are runtime bindings, not project state.
