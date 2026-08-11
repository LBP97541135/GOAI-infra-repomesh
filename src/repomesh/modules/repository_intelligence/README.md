# Repository Intelligence

Owns repository registration, scan runs, immutable profile versions, dependency evidence, and
PRD-to-repository discovery. It proposes scope with evidence; Project owns human-confirmed scope.

Public contract: `RepositorySelected`. Current infrastructure is in-memory and must be replaced
by a contract-compatible PostgreSQL implementation.

`parse_user_input` is a deprecated implementation detail retained for one compatibility window.
New callers pass the repository URL and requirement as separate fields. Cross-module plan
execution is owned and exported exclusively by `change_orchestration`.
