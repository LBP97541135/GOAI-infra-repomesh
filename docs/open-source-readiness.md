# Open Source Readiness

RepoMesh contains reusable contracts, documentation and testable orchestration components under the
Apache License, Version 2.0.

## Current Status

| Area | Status | Notes |
| --- | --- | --- |
| Root repository license | Present | Apache License, Version 2.0. |
| Third-party notices | Present | `THIRD_PARTY_NOTICES.md` records embedded and referenced projects. |
| AgentTeams subtree license | Recorded | `components/agentteams` preserves upstream Apache-2.0 notices. |
| Python package metadata | Present | `pyproject.toml` defines package name, version and dependencies. |
| Runtime contracts | Present | `contracts/runtime` contains versioned schemas and references. |
| Skill contracts | Present | `capabilities/skills` contains role-oriented Skill definitions. |
| MCP catalog | Present | `capabilities/mcp/servers.json` pins sources and runtime contracts. |
| Tests | Present | Unit and contract tests exist; local run currently depends on a healthy `.venv`. |

## Release Blockers

1. Confirm whether generated demo data under `datasets` may be redistributed.
2. Re-run `uv run ruff check .` and `uv run pytest` in a clean environment.

## Third-Party Dependency Inventory

Runtime dependencies from `pyproject.toml`:

| Dependency | Purpose |
| --- | --- |
| `alembic` | Database migrations. |
| `asyncpg` | PostgreSQL async driver. |
| `cryptography` | GitHub App signing and security primitives. |
| `fastapi` | API framework at the platform edge. |
| `httpx` | HTTP client for adapters. |
| `minio` | Object storage adapter compatibility. |
| `opentelemetry-exporter-otlp-proto-http` | Trace export. |
| `opentelemetry-sdk` | Trace and metric instrumentation. |
| `pydantic-settings` | Runtime configuration. |
| `sqlalchemy[asyncio]` | Persistence implementation. |
| `uvicorn[standard]` | Local API server. |

Development dependencies:

| Dependency | Purpose |
| --- | --- |
| `aiosqlite` | SQLite-backed async tests. |
| `pytest` | Test runner. |
| `pytest-asyncio` | Async test support. |
| `ruff` | Linting and import checks. |

Embedded component:

| Component | Source | License | Policy |
| --- | --- | --- | --- |
| AgentTeams | `https://github.com/agentscope-ai/AgentTeams.git` at `v1.2.0` | Apache-2.0 | Preserve upstream notices and record exact subtree commits. |

## Public Contribution Shape

The safest reusable artifacts to publish first are:

- Runtime v1 task, event and worker contracts.
- Skill contract templates and lifecycle policy.
- MCP catalog contract pattern.
- Mock coding adapter and orchestration tests.
- Architecture docs for module ownership, Agent identity and governed AgentTeams flow.

Code that wires real credentials, live cloud accounts, private repository URLs or customer-specific
data must stay out of public examples. Public demos should use mock adapters, synthetic repositories
and redacted traces.
