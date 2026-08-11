# RepoMesh

RepoMesh is an observable control plane for multi-repository coding-agent delivery. RepoMesh owns
projects, specifications, tasks, context, validation, change sets, recovery, and audit history.
AgentTeams is RepoMesh's first-party runtime control plane for teams, workers, skills, and
message transport.

## Current milestone

The repository contains the team, persistence, runtime integration, and Context foundations:

- A composition root that wires modules to replaceable adapters.
- Fifteen business modules with machine-readable owners and boundaries.
- A working Repository Intelligence vertical slice.
- A provider-neutral Agent Runtime port, 23 CLI adapters, and a seven-scenario mock adapter.
- AgentTeams v1.2.0 source embedded as a pinned subtree under components/agentteams.
- Runtime v1 JSON contracts and the Python RepoMesh Runner execution foundation.
- Versioned Context objects, permission intersection, immutable bundles, deltas, and access audit.
- Module-local business API routers plus platform entry points, aggregated by a behavior-free
  top-level router.
- CODEOWNERS, a pull-request checklist, adapter contract tests, and architecture tests.
- PostgreSQL persistence, Alembic migrations, transactional events, audit, outbox, and readiness.

## Run locally

```powershell
uv sync --extra dev
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn repomesh.main:app --reload
```

Open `http://127.0.0.1:8000/docs`. For the full local platform, use Docker and PowerShell 7+
to install AgentTeams from the checked-in installer and start the containerized RepoMesh API:

~~~powershell
.\scripts\start-platform.ps1 -InstallAgentTeams
~~~

On Linux:

~~~bash
./scripts/start-platform.sh --install-agentteams
~~~

Run all checks with:

```powershell
uv run ruff check .
uv run pytest
```

## Team entry points

- Documentation index: `docs/README.md`
- Team handoff and next development: `docs/development/team-handoff.md`
- Parallel work plan: `docs/development/parallel-work-plan.md`
- Public contracts: `docs/contracts/public-contracts-v0.1.md`

- Module owners and responsibilities: `docs/architecture/module-map.md`
- Dependency rules: `docs/architecture/dependency-rules.md`
- Runtime planes: `docs/architecture/runtime-planes.md`
- Database setup: `docs/database.md`
- Database ownership: `docs/architecture/database-ownership.md`
- Team workflow: `docs/architecture/team-development.md`
- Architecture decisions: `docs/adr/0001-independent-repomesh-core.md` and
  `docs/adr/0002-first-party-agentteams-runtime.md`

Each module owns its schema and implementation. Consumers may import only the producer's
`contracts` module. External systems and first-party runtime processes cross adapters under
`repomesh.integrations`; concrete implementations are selected only in
`repomesh.bootstrap`.
