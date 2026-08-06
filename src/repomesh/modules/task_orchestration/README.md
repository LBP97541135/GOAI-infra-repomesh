# Task Orchestration

Owns task specifications, state transitions, dependency graphs, READY evaluation, leases,
parallel scheduling, retries, and checkpoints. It requests Agent Runtime execution but does not
launch vendor agents directly.

## Implemented workflow

- Organization Leaders assign repository tasks only to their direct Repository Leaders.
- Repository Leaders create Worker tasks only under a parent repository task.
- Workers start and report only their own tasks.
- Reports travel back through the same Leader chain via Collaboration.
- Project progress is calculated from persisted task states, not Matrix history.
- Assignment and report delivery are idempotent and recover after a transient Matrix failure.
- Worker assignment first publishes an AgentTeams-compatible `spec.md`, `meta.json`, and
  content-hash manifest into the Team namespace; Matrix notification happens only after the
  published files are read back and verified.
- Worker assignment messages instruct the assignee to call RepoMesh `start_assigned_task`;
  repository edits are prepared and dispatched through Runner instead of the chat agent.
- PostgreSQL updates use an optimistic task version to reject concurrent overwrites.

Dependency-graph readiness, leases, parallel scheduling and retry policy remain the next layer;
the current implementation establishes the safe hierarchical execution path first.
