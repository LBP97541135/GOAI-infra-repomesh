# Repository Intelligence

Owns repository registration, scan runs, immutable profile versions, dependency evidence, and
PRD-to-repository discovery. It proposes scope with evidence; Project owns human-confirmed scope.

Public contract: `RepositorySelected`. Current infrastructure is in-memory and must be replaced
by a contract-compatible PostgreSQL implementation.
