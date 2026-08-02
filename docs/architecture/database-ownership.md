# Database Ownership

RepoMesh uses one PostgreSQL deployment initially, with one schema per business module. The
module named in the table owns migrations and writes for that schema.

Direct joins across module schemas are forbidden in application code. Read models that combine
modules are built from public queries or events and are owned by the consuming module.

Required platform schemas:

- `platform_outbox`: transactional events waiting for publication.
- `platform_inbox`: consumed event identifiers for idempotency.
- `platform_migrations`: migration metadata if the migration tool requires shared state.

Secrets are stored in an external secret manager. RepoMesh persists only credential references.
AgentTeams and Matrix messages are not a persistence mechanism for RepoMesh domain state.
