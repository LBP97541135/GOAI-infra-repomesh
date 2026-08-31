# Capability Governance Specification (Skill Registry And MCP Runtime Control)

Status: in_progress

Source inputs: [capabilities/skills/README.md](../../capabilities/skills/README.md)
("Skill Engineering Lifecycle"), [capabilities/mcp/servers.json](../../capabilities/mcp/servers.json)
(runtime contracts declared but never loaded), reviewer feedback for the second round
(prove versioned release, evaluation, canary, promotion failure, and rollback actually run).

## Problem

RepoMesh ships twelve contract-grade skills and six MCP capability definitions, but the
runtime resolves them from hard-coded presets ([presets.py](../../src/repomesh/modules/capability_management/presets.py)).
The lifecycle the skill README promises — versioned registry, evaluation gate, canary,
promotion, rollback — has no database, no API, and no code. The MCP contract's
`timeoutSeconds`, `retryPolicy`, `audit`, and `degradedMode` fields exist only as a JSON
document no Python code loads. Tracing records resource ids but nothing about which skill
version mounted or which MCP tool call happened. A reviewer can verify all three gaps by
reading the code.

## Ownership

`capability_management` owns capability lifecycle state (skill versions, evaluations, MCP
server policies) because it already owns the bundle contract. `runner` keeps owning
materialization; it only consumes resolved versions. `observability` keeps owning audit
sink; this feature emits into the existing structured-log pipeline. AgentTeams remains a
distribution pipe (MinIO/mcporter) and gains no governance role.

## Skill Version Contract

New schema `capability_management`, table `skill_versions`:

- `id` UUID pk, `skill_id` (e.g. `task-execution`), `version` (MAJOR.MINOR.PATCH string),
  `status` (`draft` → `evaluating` → `canary` → `promoted`; plus `rolled_back`),
  `local_path` (wrapper location, defaults to the preset path),
  `content_hash` (sha256 of SKILL.md at registration — immutable evidence),
  `snapshot_id` (the Skill Set snapshot that included this version, nullable),
  `created_by`, `created_at`, `updated_at`, and unique `(skill_id, version)`.

Status transitions are one-way through the pipeline and validated in the domain layer:

```text
draft -> evaluating -> canary -> promoted
                     └-> (evaluation refused) stays evaluating, evidence recorded
promoted -> rolled_back   (only via rollback)
canary -> rolled_back     (only via rollback)
```

`rolled_back` versions are excluded from every future snapshot. Re-promoting a rolled-back
version requires a NEW version number (bump), never reuse — matches the README's "Rollback
removes the new version from future Skill Set snapshots".

## Evaluation Gate

Table `skill_evaluations`: one record per evaluation attempt against a version —
`id`, `version_id`, `scenario`, `negative_case`, `outcome` (`pass`/`fail`),
`evidence` (redacted text/URL), `evaluated_by`, `created_at`.

Promotion rules, enforced server-side:

- `evaluating -> canary` requires at least one `pass` record and zero `fail` records.
- `canary -> promoted` requires at least one `pass` record created while the version was
  in `canary` (the canary org is the evidence source) and zero `fail` records.
- A `fail` record on a canary version moves it to `rolled_back` — the "晋级失败" path is a
  first-class, demonstrable transition, not an error message.

## Skill Set Snapshot

Table `skill_snapshots`: `id` UUID, `organization_id` (nullable = global), `versions`
(JSONB list of `skill_id@version`), `created_at`, `superseded_at`. Snapshot creation is
idempotent per (organization, version-set): creating a snapshot with an identical version
set returns the existing row. Materialized task contexts record `snapshot_id` in the
context manifest so a run's skill provenance is reconstructible after the fact.

## Resolution And Seeding

`PresetCapabilityAssembler` becomes the *seed*, not the runtime source: on first boot the
container seeds `skill_versions` rows (`promoted`, version `1.0.0`, preset paths) for the
twelve preset skills. The new `RegistryCapabilityAssembler` wraps the preset one: it
resolves each skill id to its current promoted version (or the canary version when the
principal's organization is on the canary list) and fails closed if a skill id has no
promoted version. Bundle shape and role checks are unchanged, so `AgentCapabilityBundle`
consumers (materializer, task projection, runtime projection) need no signature change.

## Materializer Provenance

`RunnerContextMaterializer` gains the resolved version per skill and writes
`.repomesh/skills/{skill_id}/SKILL.md` unchanged but records
`"skills": [{"id": ..., "version": ..., "contentHash": ...}]` plus `"snapshotId"` in the
manifest. The manifest schema version bumps to `repomesh.context-manifest.v2`. The v1
flat-string list is retained under `legacySkillIds` so existing readers degrade instead of
breaking.

## MCP Runtime Policy

Table `capability_management.mcp_server_policies`: `id` (server id string, pk),
`timeout_seconds` int, `max_retries` int, `retryable_only_reads` bool,
`degraded_block_writes` bool, `required_task_features` JSONB, seeded from
`servers.json` (github 30s, context7 20s, playwright 60s) plus the internal
`repomesh-task-control` (10s, no retry — it is a start action, not idempotent).

A `McpCallGuard` (new module under `capability_management`) wraps outbound MCP tool
invocations with the policy:

- **timeout**: `asyncio.wait_for` with the policy value; the call is cancelled, not leaked.
- **retry**: only when `retryable_only_reads` and the operation is classified read-only
  (operation id in the capability's `allowed_operations` minus known write verbs); retries
  share one audit id; write operations never retry without an idempotency key.
- **degraded**: when the guard is marked degraded (set by a health probe after consecutive
  timeouts), calls to operations in the server's write set are refused with a governed
  error before dispatch; read-only calls pass.
- **audit**: every call (success, timeout, retry, degraded refusal) emits one structured
  log record through the existing `logging` pipeline with
  `extra={"issue_id": ...}`-compatible context: server id, tool name, args hash (never raw
  args), outcome, latency ms, retry count, and the run/task ids already present in ambient
  context. This satisfies servers.json's `auditEveryCall: true` without a new sink.

## Trace Attributes

`src/repomesh_runner/telemetry.py` `SpanAttributes` gains `tool_name`, `skill_id`,
`skill_version`, `mcp_server`, `outcome`, `latency_ms`. The materializer wraps each skill
mount in a span carrying `skill_id`/`skill_version`; `McpCallGuard` wraps each call in a
span carrying `mcp_server`/`tool_name`/`outcome`/`latency_ms`. Both reuse the existing
`traced` decorator pattern — no new exporter or pipeline.

## API Surface

New authenticated router `capability_management.api` mounted under `/api/v1`:

- `GET /skill-versions?skill_id=` — list versions with status.
- `POST /skill-versions` — register a new version (draft).
- `POST /skill-versions/{id}/evaluations` — record an evaluation.
- `POST /skill-versions/{id}/submit-evaluation` — `evaluating` transition.
- `POST /skill-versions/{id}/canary` — enter canary (body: organization id list).
- `POST /skill-versions/{id}/promote` — evaluation-gated promotion.
- `POST /skill-versions/{id}/rollback` — from canary/promoted.
- `GET /mcp-policies` / `PUT /mcp-policies/{id}` — read and update runtime policies.

Admin authorization reuses the platform credential mechanism the bootstrap router already
uses. Refusals (illegal transition, missing evaluation evidence) are 409 with a stable
error code, following the `LeaderActionRefused` pattern.

## SLO Targets

Operational targets the existing metrics and log pipeline can verify, wired to the
`AlertingEvaluator` (an alert rule per target below is the wiring, not a new system):

| Target | Definition | SLO | Alert condition |
| --- | --- | --- | --- |
| Dispatch→start latency | runner dispatch accepted → first `runner.accepted` event | p95 < 120 s | p95 over 15 min window exceeds SLO |
| Task success rate | `succeeded` / terminal business tasks per project, 24 h | ≥ 90% | rolling rate below SLO for 1 h |
| MCP availability | `success` outcomes / total audited MCP calls per server, 15 min | ≥ 99% (github/context7), ≥ 95% (playwright) | availability below SLO, or 3 consecutive timeouts → mark degraded |
| Skill mount integrity | materialized manifests with `contentHash` mismatch | 0 | any mismatch is a page, not a metric |

Timeout→degraded escalation is the guard's health probe: three consecutive `timeout`
outcomes on one server flip it to degraded (write-refusing); a `success` clears it.

## Done When

- a version can be registered, evaluated (pass and fail), canaried, promoted, refused by
  the gate, and rolled back — with state refusals returning 409 and evidence retained;
- a fail record on canary demonstrably moves the version to `rolled_back`;
- the assembler resolves promoted versions from the database and fails closed otherwise;
- the context manifest carries `skills[].version` and `snapshotId` (v2 schema);
- MCP calls are enforced with timeout/retry/degraded/audit per stored policy;
- spans carry tool/skill/mcp attributes; SLO targets documented;
- migration up/down on PostgreSQL passes; full regression suite passes.
