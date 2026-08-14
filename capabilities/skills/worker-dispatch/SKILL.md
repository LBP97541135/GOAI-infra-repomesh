---
name: worker-dispatch
description: Assign ready repository tasks to eligible workers under policy constraints. Use when a Repository Leader must choose eligible Workers, bind ready tasks to assignees, workspaces, paths, context bundles, tools and delivery notifications.
---

# Worker Dispatch

Dispatch only ready tasks to eligible Workers. RepoMesh owns the durable assignment; AgentTeams
delivers the notification.

## Inputs

- Approved task DAG, dependency status and Repository Team membership.
- Worker identity, role, capability presets and current lease state.
- Allowed paths, required tools, context bundle reference and workspace policy.
- Matrix room and idempotency key for task notification.

## Outputs

- Worker assignment with task id, assignee id, lease, context hash and allowed paths.
- AgentTeams notification that instructs the Worker to call `start_assigned_task`.
- `BLOCKED` state when readiness, permissions or delivery prerequisites fail.

## Workflow

1. Select only tasks whose dependencies and approvals are satisfied.
2. Verify the assignee is a Worker in the repository Team and has required capabilities.
3. Check parallel tasks for write-path conflicts.
4. Persist the assignment before sending Matrix notification.
5. Send the notification with an idempotent transaction id and verify delivery.

## Dependencies

- Task orchestration, identity-access and capability-management records.
- Context bundle builder and workspace preparation policy.
- AgentTeams Team Room or Worker DM delivery path.

## Failure Handling

- Keep the task ready but undispatched when AgentTeams delivery fails.
- Reuse idempotency keys for notification retries.
- Reassign only after the current lease expires or is explicitly released.

## Safety

- Do not dispatch tasks with unsatisfied dependencies or unapproved specs.
- Do not allow Worker-to-Worker coordination to resolve write conflicts.
- Do not widen paths, tools or context during dispatch.

## Validation

- The assignment names one Worker, one task and one repository.
- The context hash and allowed paths match the approved Task Spec.
- Notification delivery is recorded or the task remains blocked.

## AgentTeams Mapping

Repository Leaders use this Skill to bind RepoMesh tasks to AgentTeams Workers. Workers receive the
message but start execution through RepoMesh MCP, not by acting directly on chat text.

