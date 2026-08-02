# RepoMesh Development Rules

## Boundaries

- Keep this repository independent from AgentTeams; integrate through adapters and contracts.
- Domain code must not depend on FastAPI, HTTP clients, databases, or vendor SDKs.
- Application services depend on ports. Infrastructure implements those ports.
- Do not use AgentTeams or Matrix messages as RepoMesh's source of truth.
- A change spanning modules must define its contract before sharing implementation details.

## Delivery

- Add tests for behavior, not directory structure.
- Preserve evidence for repository discovery decisions.
- Every external side effect needs an idempotency key or a documented retry policy.
- Keep the mock coding adapter usable so orchestration can be tested without vendor agents.
- Run `ruff check .` and `pytest` before opening a pull request.

## Suggested ownership

- Platform: `shared`, configuration, persistence, observability.
- Repository intelligence: `modules/repository_intelligence`.
- Runtime integrations: `modules/runtime` and `integrations`.
- API: `api`, depending only on application-facing contracts.
## Module changes

- Read the target module's `README.md` and `module.toml` before editing it.
- Prefer one owning module per pull request.
- Cross-module imports may target only `repomesh.modules.<producer>.contracts`.
- Business modules must not import `repomesh.bootstrap` or concrete integrations.
- Wire adapters only in the composition root after adding port contract tests.
- Update `docs/architecture/module-map.md` when ownership or responsibility changes.
- Never query or write another module's PostgreSQL schema directly.
