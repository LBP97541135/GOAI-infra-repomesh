# Review And Validation

Owns candidate reviews, review threads, test plans, immutable validation snapshots, four-level
test runs, and evidence. It never mutates a candidate frozen into an active validation snapshot.

GitHub PR approval observations are collected by Delivery as remote merge-gate facts. Review And
Validation remains the owner of RepoMesh's internal code-review content and validation evidence.

Database-changing candidates can additionally run against an isolated provider branch. The
module owns the durable lifecycle, ordered migration/backfill/verification evidence, cleanup
recovery, and binding of passed evidence to an immutable validation snapshot. Provider credentials
and live branch connections remain behind `DatabaseBranchProvider` and are never persisted here.
