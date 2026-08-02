# AgentTeams integration boundary

RepoMesh targets AgentTeams v1.2.0 at commit
`2ea027403398dfa06f3fc86445042d59f4684d71`. It does not fork or embed AgentTeams.

`control_plane.py` maps RepoMesh runtime projections to the real AgentTeams Controller API:
Managers, independent Workers, Teams that reference `workerMembers`, and convergent Worker
lifecycle operations. `matrix.py` sends tasks to AgentTeams rooms with the Matrix transaction id
as the delivery idempotency key.

RepoMesh remains the source of truth for projects, specifications, tasks, repository context,
validation and change sets. AgentTeams resources and messages are runtime projections and
observations only. See `docs/architecture/agentteams-integration.md` for the mapping and retry
policy.
