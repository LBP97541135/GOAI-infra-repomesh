# Database Branch Validation Check

- [x] Same idempotency key and same request returns one run.
- [x] Same idempotency key and different request is refused.
- [x] The branch is identified by an opaque provider reference.
- [x] Migration, backfill, and verification execute in order.
- [x] First failed command stops the run and prevents passed evidence.
- [x] Success and failure both enter cleanup.
- [x] Failed cleanup remains visible and can be retried.
- [x] Delivery evidence requires successful commands and completed cleanup.
- [x] Source database URLs/passwords are refused by the control plane.
- [x] Memory and SQLAlchemy stores round-trip the same evidence.
- [ ] Migration upgrade/downgrade is verified against a throwaway PostgreSQL database.
- [ ] **BLOCKED-EXTERNAL:** authorized Polar test tenant and Branch API credentials are available.
- [ ] **BLOCKED-EXTERNAL:** sanitized data has been approved for historical-data validation.
- [ ] **BLOCKED-EXTERNAL:** live Polar evidence names the provider branch and engine version.
- [ ] **BLOCKED-EXTERNAL:** compatibility is verified on an authorized PolarDB test target.

Current acceptance statement: the provider-neutral control plane is implemented and locally
tested. Polar Agentic Database and PolarDB production compatibility are not yet accepted.
