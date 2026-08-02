# RepoMesh

RepoMesh is an observable orchestration layer for multi-repository coding work. It owns
projects, repository intelligence, specifications, tasks, context, validation, change sets,
and audit history. AgentTeams owns agent teams, workers, managers, and message transport.

This repository is intentionally independent from AgentTeams. Integration happens through
the `repomesh.integrations.agentteams` adapter so either project can evolve independently.

## Current vertical slice

- FastAPI service with liveness and readiness endpoints.
- Repository registry and evidence-based PRD-to-repository discovery.
- Coding-agent port plus a deterministic mock adapter.
- AgentTeams HTTP client boundary.
- PostgreSQL development service and CI checks.

State is in memory in this first slice. The application ports are already separated so a
PostgreSQL implementation can replace it without changing the API or domain model.

## Run locally

```powershell
uv venv
uv pip install -e ".[dev]"
uv run uvicorn repomesh.main:app --reload
```

Open `http://127.0.0.1:8000/docs`. Useful endpoints:

- `GET /health/live`
- `GET /health/ready`
- `POST /api/v1/repositories`
- `POST /api/v1/discovery`
- `POST /api/v1/coding-runs/mock`

Run checks with `uv run ruff check .` and `uv run pytest`.

## Architecture rule

Modules may import `repomesh.shared`, but must not import another module's infrastructure.
Cross-module interaction goes through application ports or domain events. External systems
are adapters under `repomesh.integrations` or a module's `infrastructure` package.

