# Unified Recovery Case Tasks

| Task | Status | Deliverable |
| --- | --- | --- |
| U0 | complete | Projection ownership, state, actions, fencing, and security contract |
| U1 | complete | Migration 0043 and Recovery Case/Decision/Operation store |
| U2 | complete | Worker Recovery and Delivery Conflict source projection |
| U3 | complete | Admin read/preview and concurrent evidence-fenced decision API |
| U4 | complete | Leased executor; resume/reassign/retry/conflict-task handlers; safe unavailable errors |
| U5 | complete | Human Review and ChangeSet Recovery Plan projection/read model |
| U6 | pending environment acceptance | Offline PostgreSQL SQL, Ruff, focused and full regression pass; Docker unavailable for live PostgreSQL concurrency |

## Evidence

- Worker Recovery, Delivery Conflict, ChangeSet Recovery Plan, and Human Review project into one
  source-keyed Case list without replacing their authoritative records.
- Source replay updates one Case; source resolution closes it.
- Evidence changes fence old previews and decisions; concurrent decisions have one winner.
- Approved operations are leased and restart-reclaimable; Worker resume/reassign and retry handlers
  call existing governed services.
- Unregistered actions fail closed with stable codes and are not exposed as available actions.
- Recovery reads and decisions require local administrator authentication in P0.
- Migration 0043 PostgreSQL upgrade/downgrade SQL generation passes.
- Ruff passes and regression completes with `1372 passed, 21 skipped, 1 deselected`.
