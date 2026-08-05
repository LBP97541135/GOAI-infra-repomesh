# Module Ownership Map

This map is the source of truth for team ownership. Each module also has a machine-readable
`module.toml`. Replace the provisional team labels with GitHub teams when the repository moves
into an organization.

| Module | Owner | Owns | Does not own |
| --- | --- | --- | --- |
| Agent Directory | agent-platform | business principals and AgentTeams bindings | runtime configuration, processes, secrets |
| Repository Intelligence | repository-intelligence | scans, profiles, dependency evidence, discovery | confirmed project scope |
| Project | project-planning | lifecycle, repository scope, membership, workstreams | spec content, tasks |
| Specification | project-planning | specs, contracts, decisions | scheduling, execution |
| Task Orchestration | orchestration | task graph, READY, leases, retry | vendor processes, PRs |
| Context | orchestration | context versions, visibility, bundles | source worktrees, secret values |
| Agent Runtime | runtime-integrations | coding runs, sessions, resume, artifacts | task policy, provider details |
| Collaboration | orchestration | questions, answers, published findings | business state hidden in chat |
| Review And Validation | quality-delivery | reviews, test plans, snapshots, evidence | remote merge |
| Change Control | project-planning | impact assessment and change decisions | silent task mutation |
| Delivery | quality-delivery | ChangeSets, PRs, merge order, rollback | code generation |
| Observability | platform | audit, traces, metrics, cost timeline | business command handling |
| Identity And Access | platform | organizations, users, authorization, credential refs | secret storage |

## Runtime component and integration ownership

| Component / Integration | Owner | Boundary |
| --- | --- | --- |
| AgentTeams | runtime-integrations | first-party Go runtime resources, reconciliation, Matrix and Worker lifecycle |
| RepoMesh Runner | runtime-integrations | first-party Python coding execution, native sessions and runtime events |
| Coding agents | runtime-integrations | provider CLI and native session differences |
| Workspace | runtime-integrations | worktree isolation and recovery |
| SCM | quality-delivery | GitHub/GitLab repository and PR operations |
| CI | quality-delivery | check observation, job trigger, failure logs |

## Change protocol

1. The producing module changes its public contract and contract tests first.
2. Consumers depend only on `repomesh.modules.<module>.contracts`.
3. Database tables, ORM models, and infrastructure classes are never cross-module APIs.
4. A public contract change requires review from both producer and affected consumers.
