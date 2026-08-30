# Dynamic Plan Revision Tasks

| Task | Status | Deliverable |
| --- | --- | --- |
| D0 | complete | Append-only, DAG, approval, and evidence contract frozen |
| D1 | complete | Repository-identity plan items and Migration 0041 revision journal |
| D2 | complete | Atomic preview/commit service, immutable idempotency, revision history |
| D3 | complete | Prefix-preserving topological append batches and cycle validation |
| D4 | complete | Existing Advancer dispatches appended work once after current batch |
| D5 | complete | Preview permits scope review; commit refuses unapproved repositories; authenticated API |
| D6 | pending environment acceptance | Offline PostgreSQL upgrade/downgrade SQL, Ruff, and full regression pass; Docker unavailable for live PostgreSQL execution |

## Evidence

- Preview is side-effect free; commit and immutable idempotent replay pass.
- Existing batches remain byte-for-byte equivalent and appended dependencies form new tail batches.
- Existing repository edits, missing dependencies, self-dependencies, and cycles fail closed.
- The ordinary Advancer dispatches an appended batch once after its predecessor succeeds.
- Scope expansion can be previewed but cannot commit before project topology approval.
- Ruff passes and regression completes with `1365 passed, 21 skipped, 1 deselected`.
- Migration 0041 PostgreSQL upgrade/downgrade SQL generation passes; live execution awaits Docker.
