# Delivery Conflict Recovery Tasks

| Task | Status | Deliverable |
| --- | --- | --- |
| C0 | complete | Preserve merge ordering; freeze conflict Case and recovery contract |
| C1 | complete | Migration 0042, active-case uniqueness, versioned durable store |
| C2 | complete | Reconciler detects base drift and unmergeable PR before CI projection |
| C3 | complete | Merge Gate blocker and idempotent same-Team repair Task |
| C4 | complete | Candidate revision resolves Case; Head-bound CI/review/validation reset |
| C5 | complete | Authenticated Case API, structured logs, no-Worker human escalation |
| C6 | pending environment acceptance | Offline migration SQL, Ruff, focused and full regression pass; Docker unavailable for live PostgreSQL/SCM execution |

## Evidence

- Existing dependency merge order and merge cursor remain unchanged.
- Reconciler detects Base SHA drift and `mergeable=false` before projecting CI/reviews.
- One active Case per ChangeSet/repository closes the Merge Gate and survives replay.
- Same-Team repair assignment is idempotent; missing Worker produces an exception review request.
- Candidate revision resolves the old Case and existing Head-bound gates require fresh evidence.
- Authenticated Conflict Case read API and structured logs are available.
- Ruff passes and regression completes with `1367 passed, 21 skipped, 1 deselected`.
- Migration 0042 PostgreSQL upgrade/downgrade SQL generation passes; live execution awaits Docker.
