# RepoMesh

RepoMesh is an observable control plane for multi-repository coding-agent delivery. RepoMesh owns
projects, specifications, tasks, context, validation, change sets, recovery, and audit history.
AgentTeams owns agent teams, managers, workers, skills, and message transport.

## Current milestone

The repository contains the Milestone 0 team foundation and Milestone 1 database foundation:

- A composition root that wires modules to replaceable adapters.
- Thirteen business modules with machine-readable owners and boundaries.
- A working Repository Intelligence vertical slice.
- A provider-neutral Agent Runtime port and seven-scenario mock adapter.
- Module-local API routers aggregated by a behavior-free top-level router.
- CODEOWNERS, a pull-request checklist, adapter contract tests, and architecture tests.
- PostgreSQL persistence, Alembic migrations, transactional events, audit, outbox, and readiness.

## Run locally

```powershell
uv sync --extra dev
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn repomesh.main:app --reload
```

Open `http://127.0.0.1:8000/docs`. Run all checks with:

```powershell
uv run ruff check .
uv run pytest
```

## Team entry points

- Module owners and responsibilities: `docs/architecture/module-map.md`
- Dependency rules: `docs/architecture/dependency-rules.md`
- Database setup: `docs/database.md`
- Database ownership: `docs/architecture/database-ownership.md`
- Team workflow: `docs/architecture/team-development.md`
- Architecture decision: `docs/adr/0001-independent-repomesh-core.md`

Each module owns its schema and implementation. Consumers may import only the producer's
`contracts` module. External systems are adapters under `repomesh.integrations`, and concrete
implementations are selected only in `repomesh.bootstrap`.