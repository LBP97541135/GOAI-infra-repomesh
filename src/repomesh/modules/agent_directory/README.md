# Agent Directory

Owns `AgentPrincipal` business identities and their bindings to native AgentTeams resources. It
does not own models, runtimes, prompts, Skills, MCP servers, Matrix identities, rooms or runtime
state; AgentTeams is the source of truth for those fields.

## Business hierarchy

```text
Organization Leader (one per organization, bound to AgentTeams Manager)
  -> Repository Leader (one per repository, bound to AgentTeams Worker)
    -> Workers (bound to AgentTeams Workers)
```

RepoMesh retains only information AgentTeams does not model: organization, durable Leader chain,
repository ownership, responsibility paths and enabled/disabled business status. Context
visibility is derived from role, project membership, Task and Run delegation instead of being
copied into every principal. A native AgentTeams Manager/Worker binding is unique and cannot be
claimed by two RepoMesh principals.

## Registration

AgentTeams resources are created and configured through its Controller API first. `CreateAgent`
then registers the business principal with `agentteams_resource_name`; the native resource kind is
derived from the RepoMesh role and retained only as a database binding constraint. Repository
onboarding registers one existing Worker as Repository Leader and one to twenty existing Workers
as its pool. Idempotency and database singleton keys prevent duplicate leaders and duplicate
native bindings.

PostgreSQL table `agent_directory.agent_principals` is the business identity source of truth.
AgentTeams remains the runtime identity and configuration source of truth.

## Authorization boundary

Role policy defines the durable context ceiling. Identity and Access intersects it with project
membership, Task and Run delegation. RepoMesh Runner separately enforces repository paths, coding
tools, tests and network access. AgentTeams Skills/MCP/channel policy are not copied into the
principal record.
