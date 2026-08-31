# Worker Execution Leasing Tasks

Source: [Worker execution leasing specification](worker-execution-leasing-spec.md)

| Task | Status | Deliverable |
| --- | --- | --- |
| W0 | complete | Reservation, one-slot capacity, lease, and fencing contract frozen |
| W1 | complete | Migration 0039, reservation contract, partial unique indexes, and PostgreSQL store |
| W2 | complete | Start action reserves before preparation; concurrent replay returns one run |
| W3 | complete | Preparation failure and Runner terminal events release active Task/Worker guards |
| W4 | complete | Preparation renewal, expiry recovery, incremented attempts, and stale-run fencing |
| W5 | complete | Exact project binding, cross-project Worker slot, isolated run ids, and existing live-head delivery guard |
| W6 | complete | PostgreSQL concurrency/migration and full regression passed |

## Current Evidence

- 32 concurrent PostgreSQL reservations returned one created row and one run id.
- A Worker already active in one project was rejected for another project's Task.
- An expired preparation produced a new run and attempt; the stale run remained fenced.
- Concurrent start requests created one Worktree, Context Bundle, and Runner dispatch.
- Runner terminal events released the active Task and Worker partial-index guards.
- Migration `0038 -> 0039 -> 0038 -> 0039` passed on PostgreSQL 16.
- Ruff passed and the full repository suite completed with `1356 passed, 20 skipped`.

Implementation order: `W0 -> W1 -> W2 -> W3 -> W4 -> W5 -> W6`.
