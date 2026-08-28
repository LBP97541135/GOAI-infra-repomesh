---
name: task-execution
description: Execute only the currently assigned RepoMesh task. Use when a Worker receives a RepoMesh task assignment and must start a governed Runner execution through the approved MCP entry without expanding scope, changing specs, creating PRs, or contacting peer Workers.
---

# Task Execution

Execute one assigned task through RepoMesh Runner. The Worker supplies only the permitted start
parameters; RepoMesh creates the run, context bundle, workspace and dispatch.

## Inputs

- Assigned `task_id`, `worker_agent_id` and approved `adapter_id`.
- Task notification containing the immutable context bundle reference and hash.
- Approved allowed paths, allowed tools and test commands.

## Outputs

- Runner dispatch id and execution events.
- Candidate commit only when path policy and tests pass.
- Task result summary with changed files, commit SHA, tests, artifacts and blockers.

## Workflow

1. Call `repomesh-task-control.start_assigned_task` with only `task_id`, `worker_agent_id` and
   `adapter_id`.
2. Read the materialized Task Spec, allowed paths and acceptance criteria from the Runner workspace.
3. Make only the task-scoped code changes.
4. Run required tests and preserve evidence.
5. Submit the structured result and release task-local context.

## Dependencies

- RepoMesh Worker MCP, Runner, coding-agent adapter and workspace manager.
- Approved context bundle and task specification.
- Repository-local test tools allowed by policy.

## Failure Handling

- Report a blocker when MCP start, workspace preparation, permissions or tests fail.
- Do not retry non-idempotent external actions without a retry policy.
- Preserve failed workspaces and logs for Repository Leader diagnosis.

## Safety

- Do not generate a run id, context bundle, workspace path or broaden execution parameters.
- Do not edit specs, create PRs, merge, change credentials or contact peer Workers.
- Do not write outside delegated paths or bypass Runner permissions.

## Validation

- The result references the run id, context hash and commit SHA produced by RepoMesh.
- Every changed path is allowed and staged by Runner after tests pass.
- Required tests are attached with command and exit code.

## AgentTeams Mapping

Workers use this Skill after Repository Leaders notify them in the AgentTeams Team Room. The
AgentTeams message is a dispatch signal; RepoMesh MCP remains the execution authority.
