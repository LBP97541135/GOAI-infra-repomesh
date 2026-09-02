# Agent Runtime

Owns CodingRun lifecycle, provider-neutral execution requests/results, agent sessions,
interrupt/resume checkpoints, and collected runtime artifacts. Provider CLI behavior belongs in
`repomesh.integrations.coding_agents`; Task Orchestration owns task state.

Public contract: `CodingRunFinished`. The Scenario Mock is the current reference adapter.
## Authorized execution

`ExecuteAuthorizedCodingRun` is the control-plane gate in front of a coding provider. It requires
an immutable `ExecutionContextGrant` bound to the same Agent, Project, Repository and Run. The
gate rejects expired grants, undelegated tools, denied paths and paths outside the grant before a
provider is invoked.

This gate does not replace Runner sandboxing. The Runner must still enforce filesystem, process,
network and secret restrictions so provider behavior cannot bypass the control-plane decision.

## Worker start action

`POST /api/v1/agent-actions/start-worker-task` is the internal HTTP trigger. The equivalent
AgentTeams-native entry is the `start_assigned_task` tool at `POST /api/v1/mcp/worker`. A Worker
provides only its assigned Task id, Worker id and coding-adapter id. RepoMesh derives a fresh Run,
prepares the run-scoped Worktree, creates and publishes the immutable Context Bundle, materializes
the coding package and enqueues one durable Runner task. Preparation failures persist `BLOCKED`;
when Matrix is configured the same failure is also reported to the Repository Leader.

Direct HTTP uses `REPOMESH_AGENT_ACTION_TOKEN`. In AgentTeams deployments,
`REPOMESH_WORKER_TASK_CONTROL_URL` must be the full Higress MCP route and the route must inject
`X-RepoMesh-Gateway-Token` matching `REPOMESH_MCP_GATEWAY_TOKEN` after validating the Worker's
consumer key. This preserves AgentTeams per-Worker authorization without exposing the upstream
RepoMesh credential to the Worker.

For local-only validation without Higress, set
`REPOMESH_DIRECT_WORKER_MCP_ENABLED=true`, keep `REPOMESH_ENVIRONMENT` as `development` or `test`,
and point `REPOMESH_WORKER_TASK_CONTROL_URL` directly at RepoMesh. Production refuses this mode.

Before preparing a Worktree or Context Bundle, the start action atomically creates a durable
execution reservation. Partial unique indexes allow one active execution per Task and one active
Task per Worker. Concurrent retries wait for and return the winner's Runner payload, so they do not
create duplicate runs or consume a second Worker slot. Runner terminal events release both unique
guards in the same transaction that closes the dispatch.

After a successful coding turn, the Runner validates every changed path, runs all Task Spec test
commands, stages only the validated changed files and creates a Commit as `RepoMesh Worker`. The
terminal event and Task result evidence include the full `commitSha`. Failed tests, forbidden paths
or Git errors never produce a Commit.
