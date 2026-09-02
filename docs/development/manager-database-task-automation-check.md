# Manager Database Task Automation Check

- [x] Explicit no-change and legacy undeclared are distinct.
- [x] Manager database requirement survives PostgreSQL round-trip.
- [x] Worker receives the same immutable requirement in meta/spec and permit paths.
- [x] Database-required Task without commit evidence cannot trigger validation.
- [x] Missing migration, backfill, or required check rejects Worker success.
- [x] Undeclared database diff requests Manager review and rejects success.
- [x] Complete evidence produces an automatic idempotent validation request.
- [x] Validation request is bound to organization, project, repository, Task, and candidate SHA.
- [x] Worker report cannot modify the stored Manager declaration.
- [x] Existing Validation Snapshot binding prevents evidence reuse across SHA values.
- [ ] Repository-specific ORM/query path detectors are loaded from repository configuration.
- [ ] Live Polar provider executes the generated request.
