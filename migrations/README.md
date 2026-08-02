# Database migrations

PostgreSQL is RepoMesh's business fact source. Run migrations before starting the API:

```powershell
uv run alembic upgrade head
```

Migrations create one schema per business module and a `platform` schema for state events,
audit events, outbox delivery, trace links, and idempotency records. A module owns migrations for
tables in its schema. Do not use ORM `create_all` outside tests.
