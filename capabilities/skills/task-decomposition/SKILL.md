---
name: task-decomposition
description: Decompose an approved repository specification into executable tasks. Use when a Repository Leader must turn an approved repository Engineering Spec into Worker tasks with dependencies, allowed paths, tools, acceptance criteria and test commands.
---

# Task Decomposition

Create executable Worker tasks only from an approved repository spec. Each task should be small
enough to validate independently and safe to run in an isolated workspace.

## Inputs

- Approved repository Engineering Spec and linked contracts.
- Repository dependency graph, module ownership and allowed paths.
- Worker capability presets, MCP/tool needs and test commands.
- Cross-repository dependency order from project planning.

## Outputs

- Task DAG with one assignee per executable coding task.
- Per-task instruction, acceptance criteria, allowed paths, tools and dependencies.
- Required test commands and evidence requirements.
- Blockers for unresolved contracts or missing permissions.

## Workflow

1. Split tasks by independently reviewable behavior, not by directory shape.
2. Put contract producers before consumers.
3. Assign allowed paths narrowly and avoid write conflicts across parallel tasks.
4. Attach commands that verify the task's behavior and relevant regressions.
5. Persist the DAG before any Worker execution starts.

## Dependencies

- Task orchestration, specification and capability-management records.
- Repository intelligence evidence and module metadata.
- AgentTeams Worker availability for the repository Team.

## Failure Handling

- Block decomposition when the spec is not approved.
- Escalate if a task would require cross-repository write access.
- Replan instead of mutating an in-flight Worker task when dependencies change.

## Safety

- Do not grant a Worker another repository's schema, credentials or write paths.
- Do not decompose around private implementation details from another module.
- Do not create tasks that require direct database writes across module schemas.

## Validation

- Every task maps to spec acceptance and has at least one verification command.
- Parallel tasks have no overlapping write paths unless explicitly serialized.
- Dependencies explain both technical order and review order.

## AgentTeams Mapping

Repository Leaders use this Skill to create Worker assignments inside one AgentTeams Team. Workers
receive immutable task references and start execution through RepoMesh MCP.

