# Multi-Project Execution Isolation Specification

Status: accepted for staged implementation

## Existing Boundary

Tasks, context bundles, dispatches, approvals, validation, and ChangeSets carry organization,
project, repository, and task identities. A repository Team and Matrix rooms are intentionally
shared when multiple projects use the same repository.

## Required Guards

- Reservation bindings must exactly match the Task's organization/project/repository/Worker.
- Runner events must match the reservation and dispatch binding.
- Shared-room messages require project, repository, task, execution, and attempt metadata.
- Project-scoped reads and approvals must not infer scope from a shared room alone.
- One Worker slot applies across projects, preventing hidden cross-project overload.
- Different repositories may run concurrently.
- Same-repository runs use separate worktrees but record their starting base SHA.
- Delivery rechecks the live base/head and invalidates stale validation before merge.

## Acceptance Scenarios

- two projects on different repositories run concurrently;
- two projects sharing a repository use separate task/context/workspace bindings;
- one Worker receiving both projects runs only one task at a time;
- project A events and approvals cannot update project B;
- a main-branch change invalidates the other project's stale delivery evidence.
