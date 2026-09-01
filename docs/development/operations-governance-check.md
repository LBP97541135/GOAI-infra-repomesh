# Operations Governance Check

- [x] Capacity below 80% is available.
- [x] Capacity at 80% is pressured.
- [x] Capacity at 100% is saturated and returns retry-after.
- [x] Unknown capacity fails closed in production.
- [x] Alert reevaluation cannot duplicate notification/action operations.
- [x] Notification payloads contain no prompt, token, arguments, or exception text.
- [x] Automatic actions are reversible and limited to `degrade_writes`/`pause_intake`.
- [x] `pause_intake` blocks issue creation with 503 and Retry-After.
- [x] Retention is independently configurable and bounded per pass.
- [x] Active alerts and business audit evidence are never retention targets.
- [x] Issue correlation labels trace relationships as approximate.
- [x] Repository Alembic single-head status is discovered rather than hard-coded.
- [x] Missing backup/restore infrastructure is blocked-external, not passed.
- [ ] Live capacity count ports are wired for Runner, SCM, Bootstrap, and AgentTeams.
- [ ] A real notification channel is configured and accepted.
- [ ] Database current revision is compared with the code head before upgrade.
- [ ] A backup and restore drill has passed in a deployment environment.
