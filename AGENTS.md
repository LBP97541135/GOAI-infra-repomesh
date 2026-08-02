# RepoMesh Development Rules

## Boundaries

- Treat AgentTeams as a first-party runtime component while preserving process, data, and
  contract boundaries.
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
- Runtime integrations: `modules/agent_runtime`, `repomesh_runner`, and `integrations`.
- API: `api`, depending only on application-facing contracts.

## Module changes

- Read the target module's `README.md` and `module.toml` before editing it.
- Prefer one owning module per pull request.
- Cross-module imports may target only `repomesh.modules.<producer>.contracts`.
- Business modules must not import `repomesh.bootstrap` or concrete integrations.
- Wire adapters only in the composition root after adding port contract tests.
- Update `docs/architecture/module-map.md` when ownership or responsibility changes.
- Never query or write another module's PostgreSQL schema directly.

## Embedded components

- AgentTeams is first-party product source under `components/agentteams` and is maintained
  with git subtree.
- Read `components/agentteams/AGENTS.md` before changing any upstream-owned source.
- RepoMesh business modules must not import AgentTeams Go internals or component files.
- Keep HTTP, Matrix, Worker runtime, and plugin contracts as the integration boundary.
- Record exact product-fork and official-upstream commits for every AgentTeams update.
- Define or update `contracts/runtime` before a cross-plane change.
- Prefer a RepoMesh Runner or existing extension point; patch the Controller when reconciliation
  or enforcement belongs in Go.
