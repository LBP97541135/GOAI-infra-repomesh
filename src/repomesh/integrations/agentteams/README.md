# AgentTeams integration boundary

RepoMesh targets AgentTeams v1.2.0 at commit
`793db242257a569d911b1aa59c1cd554af78511f`. The complete upstream source is embedded at
`components/agentteams` through a pinned git subtree. The Python integration still treats
AgentTeams as an independent runtime control plane.

`control_plane.py` maps RepoMesh runtime projections to the real AgentTeams Controller API:
Managers, independent Workers, Teams that reference `workerMembers`, and convergent Worker
lifecycle operations. `matrix.py` sends tasks to AgentTeams rooms with the Matrix transaction id
as the delivery idempotency key.

RepoMesh remains the source of truth for projects, specifications, tasks, repository context,
validation and change sets. AgentTeams resources and messages are runtime projections and
observations only. See `docs/architecture/agentteams-integration.md` for the mapping and retry
policy.
