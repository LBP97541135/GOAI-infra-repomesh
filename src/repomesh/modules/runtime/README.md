# Runtime

Planned boundary for runtime-wide contracts that are shared across AgentTeams, RepoMesh Runner,
coding-agent adapters and gateway policy. Active execution state currently remains in
`agent_runtime`; process execution remains in `repomesh_runner`; external runtime adapters remain
under `repomesh.integrations`.

This module must not import concrete AgentTeams source, vendor SDKs, HTTP clients or persistence
adapters. Promote a contract here only when more than one runtime integration plane needs the same
stable type before sharing implementation details.
