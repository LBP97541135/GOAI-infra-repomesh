# Change Control

Owns versioned requests that alter scope, goals, acceptance criteria, contracts, or permissions,
plus impact assessments and approve/reject/replan decisions. Active task specifications must not
be changed silently.

This module owns the governance decision, not execution sequencing. Approved decisions are
consumed by `change_orchestration`, which materializes or replans repository work. The module is
currently planned; callers must not assume that its persistence and approval workflow exist yet.
