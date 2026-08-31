# Capability Governance Tasks

Source spec: [capability-governance specification](capability-governance-spec.md)

## Current Status

| Task | Status | Evidence |
| --- | --- | --- |
| G0 | complete | Spec and task list committed |
| G1 | complete | Migration 0044; domain state machine, evaluation gate, snapshot service; SQLite lifecycle tests incl. promotion-failure and rollback paths |
| G2 | complete | API router with 409-coded refusals; seeding of 12 preset skills at 1.0.0; RegistryCapabilityAssembler fail-closed resolution; manifest v2 with skill versions and snapshotId |
| G3 | complete | McpCallGuard timeout/retry/degraded/audit per stored policy; policies seeded from servers.json; audit records via structured logging |
| G4 | complete | Span attributes tool_name/skill_id/skill_version/mcp_server/outcome/latency_ms; SLO section added to spec docs |
| G5 | complete | 2306 passed / 31 skipped; 24 new tests green; ruff clean; the 3 agent_bridge failures are a pre-existing GBK-decoding environment issue (reproduced with the change set stashed). PostgreSQL migration live run pending Docker |

## Delivery Rules

- State transitions validated in the domain layer, never in the API layer alone.
- The evaluation gate refuses promotion server-side; a fail record on canary rolls back.
- Audit records carry hashes, never raw arguments or credentials.
- No skill content is fetched at runtime; materialization reads reviewed local files only.

## Dependency Order

```text
G0 -> G1 -> G2 -> G3 -> G4 -> G5
```

## Checklist

- [x] G1: `capability_management` schema, `skill_versions`, `skill_evaluations`, `skill_snapshots`, `mcp_server_policies` tables
- [x] G1: transition guard (draft→evaluating→canary→promoted, rollback from canary/promoted, refused-reuse after rollback)
- [x] G1: evaluation gate (canary needs a pass; promote needs a canary-window pass; fail on canary ⇒ rolled_back)
- [x] G1: idempotent snapshot creation per (organization, version-set)
- [x] G2: seed presets as promoted 1.0.0 on boot
- [x] G2: RegistryCapabilityAssembler: promoted default, canary per organization, fail-closed
- [x] G2: manifest v2 (`skills[].version`, `snapshotId`, `legacySkillIds` retained)
- [x] G2: lifecycle API with stable 409 codes
- [x] G3: mcp_server_policies seeded (github 30s, context7 20s, playwright 60s, task-control 10s/no-retry)
- [x] G3: McpCallGuard timeout via wait_for, read-only retry with shared audit id, degraded refuses writes, audit on every call
- [x] G4: SpanAttributes extended; spans at materializer and guard
- [x] G4: SLO targets documented (dispatch→start latency, task success rate, MCP availability)
- [x] G5: PostgreSQL migration up/down live run (20260830: 0035→0049 applied to the compose Postgres; up only — down not exercised on live data)
- [ ] G5: clean-machine style end-to-end demo script (register → evaluate fail → rollback; register → evaluate → canary → promote)
