# Operations Governance Tasks

| ID | Status | Task |
|---|---|---|
| OPS-1 | complete | Freeze policy, capacity, action, retention, correlation, and readiness contracts |
| OPS-2 | partial | Capacity/backpressure decision service complete; live Runner/SCM/AgentTeams count ports pending |
| OPS-3 | complete | Persist notification and automatic-action operations idempotently |
| OPS-4 | partial | Logging notification and pause-intake action complete; external channel adapter pending |
| OPS-5 | complete | Implement bounded usage/log/trace retention cleanup |
| OPS-6 | complete | Expose operational status, retention, and correlation APIs |
| OPS-7 | partial | Issue correlation complete; task/run/trace/correlation identifiers pending |
| OPS-8 | partial | Code migration-head and backup/restore evidence checks complete; live deployment checks pending |
| OPS-9 | blocked-external | Execute restore drill against deployment backup infrastructure |

OPS-9 requires an actual deployment backup target and cannot be accepted from unit tests.

The first operational action is wired to `POST /api/v1/issues`: a firing `pause_intake` action
returns 503 with Retry-After before a new project is created. Running tasks and recovery paths are
not stopped. `degrade_writes` is represented and composed safely but its optional external write
gates remain a follow-up per owning module.
