# Skill Registry Tasks

| Task | Status | Deliverable |
| --- | --- | --- |
| S0 | complete | Registry identity, version, release, assignment, and fallback contract |
| S1 | complete | Migration 0044 and immutable Registry persistence |
| S2 | complete | Reviewed static preset bootstrap and runtime fallback/resolution |
| S3 | complete | Evaluation gate, canary/stable promotion, rejection/deprecation states |
| S4 | complete | Stable deterministic canary assignment and Runner/manifest evidence |
| S5 | pending environment acceptance | Automatic rollback, admin APIs, trace attributes, offline migration and full regression pass; live PostgreSQL concurrency awaits Docker |

## Evidence

- Checked-in reviewed wrappers bootstrap as trusted `1.0.0` stable releases.
- Later versions require recorded evaluation thresholds before canary or stable promotion.
- Task allocation is deterministic and immutable across retries; concurrent seed/assignment recovery
  is covered by a PostgreSQL integration test.
- Failed canary health stops traffic and marks its version/release rolled back; passing canary can
  promote stable and deprecates the previous stable version.
- Wrapper path and content hash are verified both at version creation and workspace materialization.
- Runner runtime.v1 payload, context manifest, and OTel root span carry Skill version/release/
  assignment evidence.
- Migration 0044 PostgreSQL upgrade/downgrade SQL generation passes.
- Ruff passes and regression completes with `1376 passed, 22 skipped, 1 deselected`.
