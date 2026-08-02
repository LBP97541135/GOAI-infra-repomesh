# Database foundation

PostgreSQL is RepoMesh's fact source. SQLite is used only by isolated tests and is never a
supported production store.

## Local setup

```powershell
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn repomesh.main:app --reload
```

The default connection is configured by `REPOMESH_DATABASE_URL`. Applications never call
`create_all`; all non-test environments must use Alembic.

## Implemented schemas and tables

- One empty schema is reserved for each of the thirteen business modules.
- `repository_intelligence.repositories` stores the currently implemented repository registry.
- `platform.state_events` stores append-only business state facts.
- `platform.audit_events` stores append-only audit evidence.
- `platform.outbox_events` supports at-least-once external event publication.
- `platform.trace_links` links business records to trace/span identifiers.
- `platform.idempotency_records` reserves command-scope idempotency keys.

Repository registration writes the repository, state event, audit event, and outbox event in one
transaction. A uniqueness violation rolls the entire transaction back.

## Adding a module table

1. Add the SQLAlchemy model under the owning module's `infrastructure` package.
2. Import it in `migrations/env.py` so Alembic sees its metadata.
3. Generate and inspect a migration; never rely on automatic generation without review.
4. Add a SQLite adapter contract test and a PostgreSQL integration test when dialect behavior
   matters.
5. Do not create foreign keys or direct queries across module schemas. Integrate through public
   contracts and events.

Large logs, patches, reports, and binary artifacts belong in S3/MinIO. Git owns source history;
AgentTeams/Matrix own runtime communication, not RepoMesh business state.
