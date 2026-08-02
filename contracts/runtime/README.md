# Runtime Contracts

Runtime contracts connect the RepoMesh product control plane, AgentTeams runtime control plane,
and RepoMesh Runner execution plane. New incompatible shapes use a new sibling version directory;
deployed consumers must never infer fields that are absent from their declared version.

Current version: `v1`.
