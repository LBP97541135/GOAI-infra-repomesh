# Product Components

This directory contains product components that are developed and released from the RepoMesh
monorepo but keep their own runtime and language boundaries.

## AgentTeams

`components/agentteams` is imported from `agentscope-ai/AgentTeams` with `git subtree`. It provides
the Go control plane, Manager/Worker/Team resources, Matrix collaboration, managed MinIO, gateway,
and Worker container lifecycle.

RepoMesh business modules must not import AgentTeams source code. They depend on provider-neutral
ports, while `repomesh.integrations.agentteams` talks to the embedded component over its HTTP and
Matrix APIs. See `docs/architecture/agentteams-monorepo.md` for provenance and update commands.
