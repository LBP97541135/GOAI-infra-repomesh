# Worker Execution Reservation And Leasing Specification

Status: complete

## Problem

`StartAssignedWorkerTask` checks the durable Runner queue before it creates a run. Two concurrent
requests can both observe no active dispatch, then independently create worktrees, context bundles,
and Runner runs. The Runner queue is durable but is written too late to arbitrate preparation.

## Ownership

`agent_runtime` owns execution reservations because they describe Coding Run lifecycle. Task state
remains owned by `task_orchestration`; a reservation never changes task assignment by itself.

## Reservation Contract

Before preparing external or filesystem resources, the start service calls
`reserve(task, worker, lease_owner)`. PostgreSQL creates one active reservation with a preallocated
`run_id`, attempt, owner, expiry, and fencing version.

Partial unique indexes enforce:

- at most one active reservation per `task_id`;
- at most one active reservation per `worker_agent_id` for the P0 one-slot capacity policy.

Concurrent replays return the existing reservation. The winner prepares the worktree and context,
binds the immutable Runner payload, and dispatches it. Replays wait for the bound payload and return
the same run rather than preparing another one.

## States

`preparing -> running -> succeeded|failed|cancelled|expired`.

Preparation failure records `failed` and releases task/worker uniqueness. Runner terminal events
close the reservation in the same database transaction that closes the dispatch.

## Leasing And Fencing

Preparing and running reservations carry `lease_owner`, `lease_expires_at`, and `version`. Bind,
renew, fail, and completion validate owner and fencing version. An expired reservation may be
reclaimed with a higher attempt and version; stale owners cannot publish or complete it.

## Transaction Boundary

No worktree operation, context publication, model call, or Runner call occurs while a database row
lock is held. Reservation transactions perform only indexed reads and bounded writes.

## Done When

- concurrent starts produce one run, worktree, context bundle, and dispatch;
- replay returns the existing run;
- one Worker cannot prepare two project tasks simultaneously;
- stale owners cannot bind or complete after recovery;
- real PostgreSQL concurrency tests pass.
