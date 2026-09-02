# Database Branch Validation Tasks

| ID | Status | Task |
|---|---|---|
| DBV-1 | complete | Define lifecycle, commands, evidence, and provider/store ports |
| DBV-2 | complete | Implement idempotent orchestration and cleanup recovery |
| DBV-3 | complete | Add memory and PostgreSQL stores plus Alembic migration |
| DBV-4 | complete | Add deterministic provider for contract and acceptance tests |
| DBV-5 | complete | Bind passed, cleaned database evidence to validation snapshots |
| DBV-6 | blocked-external | Add live Polar Agentic Database adapter and credential wiring |
| DBV-7 | blocked-external | Run sanitized historical-data migration acceptance on Polar |
| DBV-8 | blocked-external | Verify migration and query compatibility on an authorized PolarDB test target |

DBV-6 through DBV-8 require an authorized non-production Polar endpoint/account and approved test
data. The project currently has none of these. They cannot be marked complete from a local
PostgreSQL simulation, and enterprise production access must not be used as a workaround.

Local verification: one Alembic head (`20260831_0050`), repository Ruff clean, 9 focused module
tests and 49 affected-surface regression tests passed. A full-suite run was started but stopped
after roughly one third because the repository's environment-dependent tests run for a long time;
the recorded last failure passed on immediate replay.
