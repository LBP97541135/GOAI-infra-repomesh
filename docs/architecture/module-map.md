# Module Ownership Map

This map is the source of truth for team ownership. RepoMesh currently has eighteen business
modules. Each module also has a machine-readable `module.toml`; its `status` field distinguishes
active modules from planned boundaries. Replace the provisional team labels with GitHub teams
when the repository moves into an organization.

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
| Change Orchestration | orchestration | cross-repository materialization, replan and handoff coordination | repository scanning, task state, code execution, PR delivery |
| Delivery | quality-delivery | ChangeSets, Push/PR, SCM approval facts, CI gates, merge order, rollback | code generation and internal review content |
| Decision Chain | quality-delivery | chain projection (`decision_chain_nodes`), decision trace, structural similarity summaries (consumed by Repository Intelligence via `DecisionHistoryPort`, Phase 4b prompt injection) | producer events, requirement text, full event payloads |
| Observability | platform | audit, traces, metrics, cost timeline | business command handling |
| Identity And Access | platform | organizations, users, authorization, credential refs | secret storage |
| Capability Management | platform | governed MCP and Skill presets, role-scoped agent capability bundles | runtime authorization decisions |
| Recovery Management | platform | unified failure projection, human recovery decisions, durable recovery operations | source-module failure truth, arbitrary command execution |
| Runtime | runtime-integrations | planned runtime-neutral contracts, cross-plane execution policy and gateway schemas | coding run state, CLI process execution, concrete AgentTeams or vendor adapters |

## Runtime component and integration ownership

| Component / Integration | Owner | Boundary |
| --- | --- | --- |
| AgentTeams | runtime-integrations | first-party Go runtime resources, reconciliation, Matrix and Worker lifecycle |
| RepoMesh Runner | runtime-integrations | first-party Python coding execution, native sessions and runtime events |
| Room-Native Agent Bridge | runtime-integrations | operator-hosted process for one external Worker: enrollment validation, RepoMesh binding preflight, the per-worker instance claim, room membership and the local coding session; holds no AgentTeams management credential and owns no task, permission or delivery state |
| Coding agents | runtime-integrations | provider CLI and native session differences |
| Workspace | runtime-integrations | worktree isolation and recovery |
| Worker task control | runtime-integrations | AgentTeams MCP start action, automatic Run/Context creation and durable Runner dispatch |
| SCM | quality-delivery | GitHub/GitLab repository and PR operations |
| CI | quality-delivery | check observation, job trigger, failure logs |

The runtime integration composes approved Task Specifications, immutable Context Grants and
role capability presets into a Runner task. The Workspace adapter owns repository mirror caching,
run-scoped worktree creation and immutable base revision resolution; it does not own task state.

## Standard module layout

Every business module must publish `README.md` and `module.toml`. Small modules may use flat
`contracts.py`, `application.py`, `ports.py`, and `infrastructure.py` files. Once one layer needs
multiple cohesive files, convert that layer into a package instead of adding unrelated files to
the module root. Business APIs live under the owning module's `api` package; the top-level API
package is reserved for health checks, external webhooks, and router composition.

## Change protocol

1. The producing module changes its public contract and contract tests first.
2. Consumers depend only on `repomesh.modules.<module>.contracts`.
3. Database tables, ORM models, and infrastructure classes are never cross-module APIs.
4. A public contract change requires review from both producer and affected consumers.
