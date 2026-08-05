# Identity And Access

Owns organizations, users, memberships, authorization policy, action enforcement, and references
to externally stored credentials. It never persists secret values in RepoMesh domain tables.

## Agent authorization

Agent authorization separates visibility from mutation. A role ceiling is intersected with the
current project topology and repository responsibility before an action is allowed.

| Role | Visible by default | Writable by default |
| --- | --- | --- |
| Organization Leader | organization and project-shared context; repository-team status | project scope, shared context, approvals, merge and rollback decisions |
| Repository Leader | project-shared plus its own team/task context; its repository read-only | repository spec, contracts, task plans, progress, tests and Draft PR |
| Worker | selected project-shared objects plus its own task/run context | delegated repository paths and task result/progress/test evidence |

`authorize_agent` evaluates business actions such as project/team/task management, repository
read/write, Coding Agent execution, tests, PR, merge, rollback and communication reachability.
Publishing and approving context is additionally constrained by `ContextObjectType`; visibility
never implies permission to modify the visible object.

Project communication follows the native AgentTeams boundary:

```text
Organization Leader <-> Repository Leader <-> Repository Workers
```

The Organization Leader cannot message project Workers directly, and Workers cannot message peers.
Secrets are denied here and must later be issued by a dedicated Secret Gateway using purpose-bound,
short-lived credentials.
