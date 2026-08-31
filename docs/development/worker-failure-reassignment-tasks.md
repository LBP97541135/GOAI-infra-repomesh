# Worker Failure Recovery And Reassignment Tasks

Source: [Worker failure recovery specification](worker-failure-reassignment-spec.md)

| Task | Status | Deliverable |
| --- | --- | --- |
| R0 | complete | Failure evidence, recovery decisions, ownership, and fencing contract frozen |
| R1 | complete | Migration 0040, assignment attempts, execution/dispatch generation fields |
| R2 | complete | Durable history, generation-checked reopen, and atomic Task reassignment |
| R3 | complete | runtime.v1 fencing fields, rejected-event audit, and stale write-back prevention |
| R4 | complete | Native-session resume decision with input-required escalation |
| R5 | complete | Stable same-Team Worker selection, health/failure/slot filters, reassignment budget |
| R6 | complete | Unique leased recovery operations, expiry discovery, live-dispatch reconciliation |
| R7 | complete | Idempotent exception Review Request with safe error codes |
| R8 | complete | Structured recovery logs, counters, failure counts, and session-id redaction boundary |
| R9 | pending environment acceptance | PostgreSQL test exists and offline migration SQL passed; Docker service unavailable for live execution/AgentTeams stop test |

## Implementation Evidence

- Old assignment generation terminal events are stored as rejected evidence and cannot update Task.
- Resume wins when a healthy Worker has a native session; `input_required` escalates instead.
- Reassignment changes assignee and generation in one short Task Orchestration transaction.
- A recovery operation is published only after terminal Task write-back, preventing recovery/write-back races.
- Expired Execution with an active Runner dispatch is renewed rather than falsely reassigned.
- Module boundary, runtime.v1 schema, recovery, gateway, and Worker execution tests pass.
- Alembic 0040 PostgreSQL upgrade and downgrade SQL generation pass.
- Ruff passes; the regression suite passes with `1360 passed, 21 skipped, 1 deselected` when the
  repository's known midnight-crossing daily-usage assertion is excluded. The unfiltered run had
  no other remaining failure after the module boundary correction.
- Real PostgreSQL and live AgentTeams acceptance remain pending because Docker Desktop's Windows
  service cannot be started in the current permission context.

## Dependency Order

```text
R0 -> R1 -> R2 -> R3
              ├── R4 resume
              ├── R5 reassign
              └── R7 escalate
R3 + R4 + R5 + R7 -> R6 reconciler -> R8 observability -> R9 acceptance
```

## Delivery Rules

- Do not enable automatic recovery before R9 passes.
- Every reassignment is one database transaction plus post-commit side effects.
- Never change Task assignee as a side effect of merely reading Worker health.
- A late Runner event remains inspectable but cannot mutate current business state.
- Unit tests may use SQLite, but recovery ownership and unique-generation acceptance uses PostgreSQL.
