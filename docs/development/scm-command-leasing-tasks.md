# SCM Command Leasing Tasks

Source: [SCM command leasing specification](scm-command-leasing-spec.md)

| Task | Status | Deliverable |
| --- | --- | --- |
| S0 | complete | Leasing, fencing, and at-least-once convergence contract frozen |
| S1 | complete | Migration 0038 and ORM lease fields; PostgreSQL downgrade/re-upgrade passed |
| S2 | complete | Atomic `claim_batch`; owner/version checked renew, accept, and fail |
| S3 | complete | Unique Dispatcher owner, single-command acquisition, and renewal heartbeat |
| S4 | complete | Merge/undraft remote-state guards and interrupted acknowledgement convergence preserved |
| S5 | complete | Claim/renewal logging plus dispatch, lease, and renewal settings |
| S6 | complete | Contract suite, 32-way PostgreSQL claim, expiry reclaim, fencing, migration, and full regression passed |

## Acceptance Evidence

- 32 concurrent PostgreSQL claimers produced exactly one winner.
- An expired command was reclaimed with incremented attempt and fencing version.
- The stale owner could not accept after reclamation; the current owner renewed and failed safely.
- Migration `0037 -> 0038 -> 0037 -> 0038` passed on PostgreSQL 16.
- Ruff passed and the full suite completed with `1349 passed, 19 skipped`.

## Delivery Order

`S0 -> S1 -> S2 -> S3 -> S4/S5 -> S6`.

No task is complete until its behavioral tests pass. SQLite tests may cover domain behavior, but
the concurrency acceptance must use PostgreSQL because `SKIP LOCKED` is a PostgreSQL guarantee.
