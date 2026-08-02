# Context

Owns versioned context objects, visibility scopes, effective permissions, immutable run bundles,
ordered context deltas, workspace plans, and access audit records. It records exactly which
versions and hashes a run could read.

The module stores metadata in PostgreSQL and points to immutable document bodies in S3/MinIO.
It does not own source worktrees, secret values, Matrix history, or physical filesystem mounts.

## Public boundary

- contracts.py: provider-neutral scopes, actions, version/bundle references, and published events.
- ports/store.py: persistence contract for objects, versions, bundles, deltas, and access events.
- ports/workspace.py: plan/materializer boundary implemented by Runtime infrastructure.

## Implemented behavior

- immutable ContextObjectVersion, ContextBundle, and ContextDelta hashes;
- six visibility scopes and six independent context actions;
- four-layer permission intersection with explicit deny;
- sequential supplemental deltas and ChangeRequest enforcement for execution changes;
- allowed and denied access auditing;
- in-memory and PostgreSQL stores with transactional platform events;
- Alembic migration 20260802_0002.

See docs/architecture/context-management.md for the complete model and remaining integrations.