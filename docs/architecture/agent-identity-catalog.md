# Agent Identity Catalog

This catalog is the required Agent Identity submission for RepoMesh. AgentTeams provides runtime
Manager, Worker and Team resources; RepoMesh owns durable principals, project topology,
authorization, task state, evidence and delivery decisions.

## Identity Summary

| Agent identity | Runtime projection | Primary objective | Reports to | Default scope |
| --- | --- | --- | --- | --- |
| Organization Leader | AgentTeams Manager | Govern the end-to-end project from intake through delivery. | Human supervisor or project owner | Organization and project |
| Repository Leader | AgentTeams Worker with role `team_leader` | Turn project scope into repository specs, tasks, reviews and repository readiness. | Organization Leader | One repository Team |
| Worker | AgentTeams Worker with role `worker` | Execute one approved task through RepoMesh Runner and publish evidence. | Repository Leader | One assigned task/run |

## Organization Leader

| Field | Definition |
| --- | --- |
| Unique identity | One active Organization Leader per organization, bound to an AgentTeams Manager resource. |
| Responsibilities | Project intake, repository scope confirmation, cross-repository dependency planning, approval gates, ChangeSet governance, merge/rollback decisions and final user delivery. |
| Inputs | Original requirement, repository summaries, Repository Leader confirmations, validation snapshots, CI/review observations, human approvals and blockers. |
| Outputs | Project draft, confirmed repository scope, workstream assignments, cross-repository contract decisions, governance decisions and delivery reports. |
| Skills | `project-intake`, `cross-repo-planning`, `delivery-governance`; may consume `blocker-reporting` escalations. |
| MCP/tools | GitHub MCP for repository/PR facts through the credential broker; no direct secret access. |
| Visible data | Organization and project-shared context, repository-team status, accepted repository evidence and ChangeSet state. |
| Write permissions | Project scope, shared context, approvals, merge and rollback decisions. |
| Must not do | Edit repository code, run repository tests, commit, message Workers directly or use Matrix messages as source of truth. |
| Failure path | Escalate unsafe or ambiguous decisions to the human supervisor; create change-control decisions for scope/contract changes. |

## Repository Leader

| Field | Definition |
| --- | --- |
| Unique identity | One Repository Leader per participating repository, bound to an AgentTeams Worker and attached to that repository Team. |
| Responsibilities | Author repository Engineering Spec, define contracts, decompose tasks, dispatch Workers, review code/test evidence and declare repository readiness. |
| Inputs | Confirmed project scope, repository profile, module metadata, cross-repository contracts, Worker results and test evidence. |
| Outputs | Repository Engineering Spec, Task DAG, Worker assignments, review decisions, blocker escalations and repository readiness evidence. |
| Skills | `repository-spec-authoring`, `task-decomposition`, `worker-dispatch`, `code-review`, `test-review`, `worker-result-evaluation`, `blocker-reporting`. |
| MCP/tools | GitHub MCP and Context7 MCP through the broker for repository facts and documentation lookups. |
| Visible data | Project-shared context, its repository context, Team task state, Worker evidence and relevant contracts. |
| Write permissions | Repository spec, contracts, task plans, progress, tests, review result and Draft PR metadata. |
| Must not do | Edit production code, invoke coding CLI, commit, merge PRs, contact peer repository Workers directly or grant cross-repository write access. |
| Failure path | Escalate project-scope, contract, delivery or approval conflicts to the Organization Leader; reassign or retry Worker tasks for repository-local failures. |

## Worker

| Field | Definition |
| --- | --- |
| Unique identity | One or more Workers per repository Team, each bound to an AgentTeams Worker runtime and a RepoMesh `worker` principal. |
| Responsibilities | Start only assigned tasks, execute through RepoMesh Runner, modify only allowed paths, run tests and publish structured evidence. |
| Inputs | Assigned task id, worker agent id, adapter id, immutable context bundle, allowed paths/tools and acceptance criteria. |
| Outputs | Runner events, task result summary, changed files, candidate commit SHA, test evidence, artifacts and blocker reports. |
| Skills | `task-execution`, `self-test`, `blocker-reporting`; may use task-local implementation knowledge from the approved context bundle. |
| MCP/tools | `repomesh-task-control.start_assigned_task`; conditional Playwright MCP for approved `web_e2e`; repository-local tools allowed by Runner policy. |
| Visible data | Selected project-shared objects plus its own task/run context and delegated repository paths. |
| Write permissions | Isolated Runner workspace, delegated repository paths, task progress/result/test evidence. |
| Must not do | Change scope or specs, access other repositories, contact peer Workers, create PRs, merge, commit outside Runner, bypass permissions or read secrets directly. |
| Failure path | Report blockers to the Repository Leader with evidence and wait for retry, reassignment or escalation. |

## Collaboration And Context Boundaries

- Organization Leader communicates with Repository Leaders; Repository Leaders communicate with their
  repository Workers. Worker-to-Worker and Organization-Leader-to-Worker direct channels are denied.
- RepoMesh builds immutable Context Bundles from approved project, spec, task and visibility rules.
  AgentTeams room membership never grants additional RepoMesh data access.
- Every external side effect records an idempotency key or retry policy. Matrix delivery uses a
  stable transaction id; Runner commits are created only after path policy and tests pass.
- Secrets stay outside RepoMesh domain tables. Agents reference credential ids; brokers inject
  short-lived, purpose-bound credentials when policy allows.

## Verification Evidence

- Role and visibility rules: `src/repomesh/modules/identity_access`.
- Runtime projection and AgentTeams boundary: `docs/architecture/agentteams-integration.md`.
- Governed execution flow: `docs/architecture/governed-agentteams-flow.md`.
- Live end-to-end evidence: `docs/test-results/live-github-delivery-e2e-20260810.md`.
