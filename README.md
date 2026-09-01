<div align="center">

<img src="docs/assets/logo.svg" alt="RepoMesh — observable delivery control plane" width="820">

**English** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

![Apache 2.0](https://img.shields.io/badge/license-Apache_2.0-c99e52?style=flat-square&labelColor=16130d)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-c99e52?style=flat-square&labelColor=16130d&logo=python&logoColor=e9dec2)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-c99e52?style=flat-square&labelColor=16130d&logo=fastapi&logoColor=e9dec2)
![PostgreSQL 17](https://img.shields.io/badge/PostgreSQL-17-c99e52?style=flat-square&labelColor=16130d&logo=postgresql&logoColor=e9dec2)
![React 19](https://img.shields.io/badge/React-19-c99e52?style=flat-square&labelColor=16130d&logo=react&logoColor=e9dec2)
![Vite 8](https://img.shields.io/badge/Vite-8-c99e52?style=flat-square&labelColor=16130d&logo=vite&logoColor=e9dec2)
![TypeScript 6](https://img.shields.io/badge/TypeScript-6-c99e52?style=flat-square&labelColor=16130d&logo=typescript&logoColor=e9dec2)
![Docker Compose](https://img.shields.io/badge/Docker-compose-c99e52?style=flat-square&labelColor=16130d&logo=docker&logoColor=e9dec2)

**An observable control plane for multi-repository coding-agent delivery.**
Issues become plans, plans become teams of agents in real repositories, and every
gate along the way stays on the record.

|  |  |  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Works with** | Claude Code | OpenAI Codex | OpenCode | Cursor Agent | Copilot CLI | Aider | Goose |

*23 coding-agent CLIs are wired in `src/repomesh/integrations/coding_agents/catalog.py`;
the runtime port is provider-neutral, so a CLI that can take a task can be hired.*

</div>

---

RepoMesh is an observable control plane for multi-repository coding-agent delivery. RepoMesh owns
projects, specifications, tasks, context, validation, change sets, recovery, and audit history.
AgentTeams is RepoMesh's first-party runtime control plane for teams, workers, skills, and
message transport.

![The RepoMesh delivery console: a flat issue list where each issue carries its delivery phase
as a badge — plan awaiting materialize, executing, release, paused, decision pending](docs/assets/console.svg)

The other five surfaces — review desk, repositories, teams, agents, observability —
are drawn in [the console tour](docs/console-tour.md).

## Open the console

Either of these takes a fresh clone to the delivery console in your browser
with no manual configuration. They are alternatives, not steps: both claim
port 8100.

**Development launcher** — hot reload; the host needs Docker, uv and Node 20+:

```powershell
.\scripts\dev-up.ps1                # -Seed for demo data, -NoBrowser to stay put
```

```bash
./scripts/dev-up.sh                 # --seed, --no-browser
```

It starts postgres, migrates to head, serves the API on 8100 and Vite on 5280,
then opens `http://127.0.0.1:5280`. Each stage probes first and skips whatever
is already serving, so re-running the script is the normal way back to a
working stack — and nothing the script did not start is ever restarted,
migrated into or stopped. `.\scripts\dev-down.ps1` / `./scripts/dev-down.sh`
takes down only the components the launcher started, one confirmation each.

**Full-stack compose** — the host needs Docker and nothing else:

```bash
docker compose --profile console up -d --build
```

Open `http://127.0.0.1:8100`. nginx serves the built console and reverse
proxies `/api` to the API container, which migrates its own private database on
start: one origin, no CORS, no dev proxy. Demo data, once it is up:

```bash
docker compose --profile console exec console-api python scripts/seed-console-demo.py
```

`REPOMESH_CONSOLE_PORT` moves the console off 8100, `REPOMESH_POSTGRES_PORT`
moves the development database off 5432. Tear the stack down with
`docker compose --profile console down`, add `-v` to drop its database too.

The console opens on a login gate. A fresh database holds no accounts, so the
first visit goes through *initialize administrator*, and the credentials stay on
your machine. Two planes authenticate differently, on purpose: the read models
take a Bearer action token, while human control — the review desk, checkpoint
decisions — takes the session. That is why an agent token cannot approve
anything.

Status, honestly: what has been exercised is the re-entrant path (every
component already serving, everything skipped) and the compose configuration.
The cold path from an empty machine has not been run end to end yet, so if a
step fails in a way its message does not explain, say so — that message is the
deliverable as much as the command is.

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

The v1 platform API on port 8000 — not the delivery console, which is a second
instance on 8100; for that use `Open the console` above.

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

If the Docker engine restarts (or crashes) while a stack is up, re-run the
`docker compose ... up -d` command for that stack rather than waiting: the
containers restart themselves (`restart: unless-stopped`), but a dependent
service still sitting in `Created` — created while its dependency was
unhealthy — is only started by a new `up`, never on its own.

The full platform uses one default OpenAI-compatible model connection for both RepoMesh planning
and AgentTeams agents:

```dotenv
REPOMESH_MODEL_API_KEY=your-key
REPOMESH_MODEL_BASE_URL=https://api.deepseek.com/v1
REPOMESH_MODEL=deepseek-chat
```

Advanced deployments may override the AgentTeams `AGENTTEAMS_LLM_*` or RepoMesh planning
`REPOMESH_DEEPSEEK_*` variables independently. Coding-agent CLI authentication remains separate.

The startup scripts generate Runner, agent-action, and MCP gateway tokens in the ignored
`.secrets/platform.env`, load the AgentTeams controller token, and obtain a Matrix access token.
Use `GET /api/v1/setup/status` for first-run readiness and
`GET /api/v1/setup/coding-agents` to inspect installed CLI authentication. After a repository scan,
`POST /api/v1/repositories/{repository_id}/agent-team` creates its long-lived Repository Leader,
default Worker, and AgentTeams Team. Delivery policies are stored through the organization and
repository policy endpoints under `/api/v1/delivery` rather than edited in `.env`.

Run all checks with:

```powershell
uv run ruff check .
uv run pytest
```

## License

RepoMesh is licensed under the Apache License, Version 2.0. See `LICENSE`.

## What the launcher does, and how to do it by hand

`scripts/dev-up.*` is the four manual steps below in order, with a probe in
front of each one. Read this when a step fails, when you want a different
layout, or when you are changing the launcher itself.

**Why 8100.** `Run locally` starts the v1 platform API on port 8000. The
delivery console under `frontend/` does not talk to it: the Vite dev server
proxies `/api` to a **second instance of the same app on port 8100**, which
serves the delivery read model and the local identity endpoints. The port is
written into `frontend/vite.config.ts`, which is why the launcher treats 8100
and 5280 as fixed.

1. `docker compose up -d postgres` — publishes `REPOMESH_POSTGRES_PORT` (5432
   by default), matching the default DSN
   `postgresql+asyncpg://repomesh:repomesh@localhost:5432/repomesh`.
2. `uv sync --extra dev` then `uv run alembic upgrade head` — alembic reads
   `REPOMESH_DATABASE_URL`, so the migration and the server must be given the
   same value. Never point this at a database that belongs to something else.
3. Start the read-model instance with the token
   `frontend/.env.development` expects, otherwise every read-model call is 401:

   ```powershell
   $env:REPOMESH_AGENT_ACTION_TOKEN = "console-dev-token"
   uv run uvicorn repomesh.main:app --host 127.0.0.1 --port 8100
   ```

4. `cd frontend && npm install && npm run dev`, then open
   `http://127.0.0.1:5280`.

Readiness here is `/docs` (or any HTTP answer from the root), not
`/health/ready`: readiness reports 503 under this minimal configuration, so it
would report a healthy console as broken.

`frontend/README.md` ("联调后端起法") carries the same walkthrough with the
degradation notes, the seed script, and the data-source switch.

## Team entry points

- Documentation index: `docs/README.md`
- Skill lifecycle: `capabilities/skills/README.md`
- Open source readiness: `docs/open-source-readiness.md`
- Third-party notices: `THIRD_PARTY_NOTICES.md`
- Current phase plan (full GUI loop, construction complete):
  `docs/development/full-loop-plan-20260812.md`
- Team handoff (architecture sections; status sections superseded):
  `docs/development/team-handoff.md`
- Parallel work plan: `docs/development/parallel-work-plan.md`
- Public contracts: `docs/contracts/public-contracts-v0.1.md`
- Delivery read model contract: `docs/contracts/delivery-read-model-v0.1.md`
  (v0.2–v0.4 are increments, all in force)

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
