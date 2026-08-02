# Database Ownership

RepoMesh uses one PostgreSQL deployment initially, with one schema per business module. The
module named in `module.toml` owns migrations and writes for that schema.

Direct joins across module schemas are forbidden in application code. Read models that combine
modules are built from public queries or events and are owned by the consuming module.

The `platform` schema owns state events, audit events, transactional outbox records, trace links,
and idempotency records. Alembic keeps migration metadata in the default PostgreSQL schema.

Secrets are stored in an external secret manager. RepoMesh persists only credential references.
AgentTeams and Matrix messages are not a persistence mechanism for RepoMesh domain state.