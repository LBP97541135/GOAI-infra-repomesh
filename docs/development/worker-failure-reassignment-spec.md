# Worker Failure Recovery And Reassignment Specification

Status: implementation complete; PostgreSQL and live AgentTeams acceptance pending

## Background

Worker execution reservations prevent duplicate starts, but they do not decide what to do after a
Worker, Runner, or API instance disappears during execution. A Task can remain `in_progress`, while
the old Worker may later recover and submit a stale result. RepoMesh needs one durable recovery
decision that prefers resuming useful work, reassigns only when necessary, and fences every older
attempt.

## Goals

- Detect abandoned executions from durable evidence rather than one failed HTTP probe.
- Resume a verified native Agent session when its Worker and workspace remain usable.
- Reassign to a healthy Worker in the same project repository Team when resume is impossible.
- Preserve every assignment attempt and recovery decision for audit and observability.
- Reject late events and reports from superseded attempts.
- Escalate to a human when automatic recovery is unsafe or exhausted.

## Non-Goals

- Moving a Task to a Worker outside its repository Team.
- Reusing one project's Context Bundle for another project.
- Treating Matrix presence as authoritative Task state.
- Automatically resolving Git or product-level conflicts.
- Migrating an in-memory provider process between machines.

## Ownership

- `task_orchestration` owns Task assignment, assignment attempts, and reassignment policy.
- `agent_runtime` owns execution reservation, Runner dispatch, native session, and run evidence.
- `project` owns Team membership and human exception checkpoints.
- AgentTeams provides runtime health observations; it is not the source of truth for assignment.

## Terms

- **Assignment attempt**: one audited binding of a Task to a Worker.
- **Execution attempt**: one reservation/run under an assignment attempt.
- **Assignment generation**: monotonically increasing fencing value for a Task's assignee.
- **Recoverable session**: a sanitized native session id whose adapter supports resume and whose
  workspace/context binding still matches the Task.
- **Abandoned execution**: an active execution whose lease expired and whose Runner/Worker evidence
  cannot prove continued ownership.

## Persistent Contract

Add `task_assignment_attempts` under the `task_orchestration` schema:

```text
id
organization_id
project_id
repository_id
task_id
worker_agent_id
generation
state                 active|superseded|completed|failed
reason                initial|lease_expired|worker_unreachable|runner_interrupted|operator
assigned_by           agent|human|system
assigned_by_id        nullable UUID
previous_attempt_id   nullable UUID
execution_id          nullable UUID
native_session_id     nullable opaque internal id; never emitted in ordinary logs or UI
created_at
finished_at
```

Database invariants:

- one active assignment attempt per Task via a partial unique index;
- unique `(task_id, generation)`;
- generation only increases;
- an active attempt's Worker must equal `tasks.assignee_agent_id`;
- an execution reservation binds to assignment attempt id and generation.

Native session identifiers must not be placed in user-visible errors or ordinary logs.

## Failure Signals

A single failed probe is insufficient. The reconciler evaluates:

1. execution lease expiry;
2. Runner dispatch status and lease;
3. latest Runner event and native session evidence;
4. AgentTeams Worker reachability and runtime phase;
5. workspace/context availability;
6. retry and reassignment budgets.

An execution is eligible for recovery only after its lease expires. Worker unreachability must be
observed for a bounded grace period or consecutive probe threshold. A terminal Runner event is
stronger evidence than a transient AgentTeams probe.

## Recovery Decision

| Evidence | Decision |
| --- | --- |
| lease valid | no action |
| lease expired, Runner still owns a live dispatch | renew/reconcile, no reassignment |
| interrupted/input-required, resumable session and same Worker healthy | create resume execution |
| preparation expired before dispatch | retry clean preparation on same assignment |
| Worker unhealthy or session/workspace unusable, healthy teammate exists | audited reassignment |
| no healthy teammate or retry budget exhausted | human exception checkpoint |
| project cancelled or Task terminal/superseded | cancel recovery |

`input_required` does not automatically change Worker. If it represents a human approval question,
RepoMesh opens the appropriate checkpoint and resumes the same session after approval.

## Atomic Reassignment

Reassignment is one short PostgreSQL transaction:

1. lock the Task and active assignment attempt;
2. verify expected Task version, assignment generation, and expired execution fencing value;
3. validate the replacement Worker is active, belongs to the same project repository Team, and has
   a free Worker slot;
4. supersede the old assignment attempt;
5. update `tasks.assignee_agent_id` and increment Task version;
6. insert the new active assignment attempt with generation + 1;
7. append an audit/outbox event;
8. commit before publishing Matrix or Runner work.

No AgentTeams, Matrix, filesystem, model, or Runner call occurs inside this transaction.

## Worker Selection

Candidates must be active Workers from the exact project repository Team. Exclude:

- the failed Worker for the current recovery decision;
- Workers with an active execution slot;
- disabled, missing, or unreachable runtimes;
- Workers exceeding their recent failure threshold;
- Workers lacking the Task's required runtime capabilities.

P0 selection is deterministic: lowest active execution count, then fewest recent failures, then
stable Worker id order. This makes tests and audit explanations reproducible.

## Fencing And Late Results

Runner task/event envelopes add:

```text
assignmentAttemptId
assignmentGeneration
executionId
executionVersion
```

Task start, report, Runner event ingestion, Context access, and terminal completion verify these
values against the current active assignment. A stale event is persisted as rejected evidence but
must not change Task, delivery, or validation state.

The old Worker may recover and finish its local process; fencing, not process termination, is the
correctness boundary.

## Reconciler

Add an isolated background `WorkerRecoveryReconciler` with database lease ownership. It scans only
expired active executions, claims recovery decisions with `FOR UPDATE SKIP LOCKED`, and applies one
decision at a time. Repeated runs are idempotent.

Configuration:

```text
REPOMESH_WORKER_RECOVERY_ENABLED=false
REPOMESH_WORKER_RECOVERY_SCAN_INTERVAL_SECONDS=15
REPOMESH_WORKER_RECOVERY_GRACE_SECONDS=60
REPOMESH_WORKER_RECOVERY_MAX_EXECUTION_ATTEMPTS=3
REPOMESH_WORKER_RECOVERY_MAX_REASSIGNMENTS=2
```

The feature defaults off until the live recovery acceptance matrix passes.

## Human Escalation

Create an exception checkpoint when:

- no eligible replacement Worker exists;
- all candidates repeatedly fail;
- the remote runtime state is contradictory;
- the workspace contains uncommitted evidence that cannot be safely resumed;
- reassignment or execution budgets are exhausted.

The operator sees Task, failed Worker, last run, safe error code, attempted recoveries, and available
actions. Internal tokens, native session ids, and filesystem paths are redacted.

## Observability

Emit structured events and metrics:

- `worker_recovery_detected_total`
- `worker_execution_resumed_total`
- `worker_task_reassigned_total`
- `worker_recovery_escalated_total`
- `worker_recovery_duration_seconds`
- stale Runner event rejection count
- per-Worker recent failure count

Trace links preserve organization, project, repository, Task, assignment attempt, execution, run,
and replacement Worker identities.

## Security And Isolation

- Recovery never broadens repository, Context, tool, or path permissions.
- A replacement receives a newly built Context Bundle and execution grant.
- Old credentials and grants are revoked or expire before the new execution starts.
- Shared AgentTeams rooms do not authorize reassignment.
- Cross-project Worker reuse still respects the global execution slot.

## Acceptance Matrix

- Worker stops before dispatch: clean preparation retry, no duplicate run.
- Worker stops with resumable native session: same Worker/session resumes.
- Worker stops with unusable session: healthy teammate receives generation + 1.
- Old Worker reports success after reassignment: report is rejected and audited.
- Two reconcilers recover one Task: one decision and one replacement execution.
- Two projects share a Worker: only the failed Task is recovered; the other project is unchanged.
- No replacement exists: one human checkpoint is created idempotently.
- Reconciler restarts mid-decision: database state converges without a second reassignment.
- Disabled feature: no automatic reassignment occurs.

## Done When

- assignment history is durable and queryable;
- resume/reassign/escalate decisions are deterministic and idempotent;
- stale Workers cannot alter current Task state;
- real PostgreSQL concurrent recovery tests pass;
- a live AgentTeams acceptance stops Worker A and observes Worker B complete the Task;
- Ruff, full pytest, migration downgrade/upgrade, and secret scans pass.
