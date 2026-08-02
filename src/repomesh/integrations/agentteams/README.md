# AgentTeams integration boundary

RepoMesh does not fork or embed AgentTeams. This package translates RepoMesh orchestration
commands into AgentTeams team/manager/worker operations and translates runtime events back
into RepoMesh observations.

The initial client is deliberately narrow because AgentTeams API versions may differ. Before
implementing production resources, pin a tested upstream version and add contract tests against
that version. RepoMesh remains the source of truth for projects, specifications, tasks,
repository context, validations, and change sets.

