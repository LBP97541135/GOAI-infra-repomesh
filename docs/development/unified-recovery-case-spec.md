# Unified Recovery Case Specification

Status: P0 implementation complete; live PostgreSQL acceptance pending

## Goal

Provide one operator-facing and machine-executable failure workflow across Worker execution,
delivery conflicts, CI/recovery plans, and human escalation while preserving each owning module's
source-of-truth record.

## Model

`recovery_management.recovery_cases` is a projection and orchestration aggregate keyed by immutable
`source_type/source_id`. It carries organization/project/repository/task/change-set scope, severity,
safe summary, evidence version, available actions, status, version, and timestamps.

Source facts remain authoritative:

- Agent Runtime: Worker Recovery Operation;
- Delivery: Conflict Case and Recovery Plan;
- Project: Human Review Request;
- Task Orchestration: Task/assignment generation.

## State Machine

```text
detected -> automatic_recovery -> resolved
         -> awaiting_decision -> approved -> executing -> verifying -> resolved
                                               |             |
                                               +---- failed <-+
failed -> awaiting_decision (explicit retry with fresh evidence)
```

## Decision Fencing

A decision binds `case_id`, `case_version`, and `evidence_version`. One decision is accepted for one
version. Any source update bumps the Case version/evidence and makes an old preview/decision stale.

## Actions

P0 actions: `resume_session`, `reassign_worker`, `create_conflict_task`, `approve_plan_revision`,
`rollback_change_set`, `retry`, `cancel_task`, and `manual_resolution`. Available actions are
source-specific; the API never accepts arbitrary commands.

## Execution

Approved actions create a durable leased operation. HTTP returns before execution. Handlers are
registered in the composition root and call existing domain services. Retries are bounded and
idempotent. Unknown or unsafe actions fail closed.

## Security

Case summaries and errors contain safe codes only. Native session ids, credentials, filesystem
paths, provider responses, and command arguments are excluded. Read/decision APIs require local
human authentication; decisions additionally require an authorized project grant.

## Done When

- Worker and delivery conflicts create one unified Case each;
- source replay updates rather than duplicates the Case;
- preview is side-effect free and decisions reject stale evidence;
- concurrent decisions have one winner;
- approved operations survive restart and execute once effectively;
- source resolution closes the unified Case.
