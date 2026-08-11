# RepoMesh module boundaries

RepoMesh is a modular monolith. Modules own business state and publish small contracts; adapters
implement ports; the bootstrap package is the only dependency composition root.

## Ownership

| Module | Owns | Must not own |
| --- | --- | --- |
| `repository_intelligence` | repository scan, catalog, discovery, dependency graph, integrated plan input | task execution, delivery lifecycle, human checkpoint workflow |
| `change_orchestration` | cross-module change workflow, materialization, replan and handoff coordination | repository scanning, persistence adapters, Coding Agent implementation |
| `project` | project definition, team binding, human policy, checkpoints and operational status | AgentTeams transport and SCM behavior |
| `specification` | versioned Engineering, Contract, Repository and Task Specs | project scheduling and PR state |
| `task_orchestration` | task DAG, assignment, state transitions and batch advancement | Coding Agent implementation and SCM API calls |
| `agent_runtime` | authorized execution request and Runner state | task planning and project policy ownership |
| `delivery` | ChangeSet, repository delivery state, observations and commands | task decomposition and Agent communication |
| `collaboration` | governed Agent messages and delivery records | task or project state ownership |

## Dependency direction

1. Domain code may depend on its own contracts and shared primitives only.
2. Application services depend on published contracts or ports, not another module's infrastructure.
3. Infrastructure implements a module port and may depend on persistence libraries.
4. Integrations adapt external systems such as AgentTeams, Runner and GitHub.
5. `bootstrap` constructs and caches process-level services; API handlers only request them.
6. Cross-module business sequencing belongs to `change_orchestration`, not an adapter or feature module.

## Human checkpoint invariant

Production composition always supplies `ProjectCheckpointService`. Legacy constructors use
`TopologyAwareCheckpointFallback`: it permits active automatic projects only and blocks supervised
projects with `checkpoint_gateway_not_configured`. Missing wiring can therefore never disable a
configured human gate.

Plan execution is exported only by `repomesh.modules.change_orchestration`; Repository
Intelligence has no compatibility execution path.
