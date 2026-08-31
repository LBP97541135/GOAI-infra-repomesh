# Skill Canary And Rollback Acceptance Specification

Status: implementation acceptance complete; live PostgreSQL concurrency pending

- Seed static Skill v1 and promote it stable.
- Publish/evaluate v2 and start a 10 percent canary.
- Repeated Task allocation returns the same version.
- Approximately the configured deterministic cohort receives v2.
- Runner payload, manifest, and trace identify the assigned versions.
- Failed canary health marks v2 rolled_back and sends all new Tasks to v1.
- Concurrent promotion/rollback produces one stable release.
- Migration downgrade/upgrade and full regression pass.
