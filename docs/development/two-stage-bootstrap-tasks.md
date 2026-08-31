# Two-Stage Bootstrap P0 Tasks

Source spec: [Two-stage bootstrap specification](two-stage-bootstrap-spec.md)

B4 implementation details: [Bootstrap reconciler service spec](bootstrap-reconciler-implementation-spec.md)

B5 implementation details: [Runtime configuration loader spec](runtime-configuration-implementation-spec.md)

B6 implementation details: [Execution-plane reconciler spec](execution-plane-reconciler-implementation-spec.md)

B7 implementation details: [Bootstrap progress UI spec](bootstrap-progress-ui-spec.md)

B8 implementation details: [Bootstrap recovery spec](bootstrap-recovery-implementation-spec.md)

B9 acceptance details: [Clean-machine acceptance spec](clean-machine-acceptance-spec.md)

## Current Status

| Task | Status | Evidence |
| --- | --- | --- |
| B0 | complete | Domain enums, transition guard, safe error codes, and store port implemented |
| B1 | complete | Migration 0037; SQLite lifecycle tests; PostgreSQL concurrent claim and rollback passed |
| B2 | complete | Isolated Docker-only launch with empty model environment and no matching Controller reached healthy DB/API/Web/bootstrap; setup status reported automatic AgentTeams/Matrix remediation |
| B3 | complete | Admin status/retry API, model-save trigger, idempotent operation, dependency projection tests passed |
| B4 | complete | Isolated service, disabled default, lease runner, dry-run image, Compose security and container acceptance passed |
| B5 | complete | Whitelist loader, atomic writer, read-only API mount, Fernet bootstrap and real Compose API selector passed |
| B6 | complete | Safe executor, runtime cutover, label-selected restart and real existing-AgentTeams skip-path acceptance passed |
| B7 | complete | Session API, serial polling, retry action, desktop/mobile pending/failure/completed browser acceptance passed |
| B8 | complete | Retry policy, safety failure taxonomy, redactor, lease-loss cancellation and actual secret corpus scans passed |
| B9 | complete | Isolated clean minimal-plane acceptance passed; controlled first-install executor, recovery, redaction, and real existing-AgentTeams skip-path acceptance passed |

## Delivery Rules

- Complete tasks in dependency order; do not parallelize tasks that modify the same state contract.
- Every task must add behavioral tests before its status changes to complete.
- No task may introduce a browser-to-shell or API-to-arbitrary-command surface.
- Preserve the current model-configured startup path throughout the migration.

## Dependency Graph

```text
B0 contract
 ├── B1 persistence ── B3 API trigger/status ── B7 wizard
 ├── B2 minimal plane ── B4 reconciler image/service
 └── B5 runtime config ────────────────┐
                    B4 + B5 + B3 ── B6 reconciliation workflow
                                           └── B8 failure recovery
                                                   └── B9 clean acceptance
```

## Planning Summary

| Task | Primary owner | Depends on | Estimate | Main risk |
| --- | --- | --- | --- | --- |
| B0 contracts | Platform/API | none | 0.5 day | leaking infrastructure types into contracts |
| B1 persistence | Platform | B0 | 1 day | concurrent claims and lease correctness |
| B2 minimal plane | Startup/Compose | B0 | 1 day | readiness semantics during partial setup |
| B3 API trigger/status | API | B0, B1 | 1 day | duplicate operations and secret exposure |
| B4 reconciler service | Platform/Runtime | B0, B2 | 1.5 days | Docker socket blast radius |
| B5 runtime config | Bootstrap | B0, B2 | 1 day | atomicity and restart target selection |
| B6 reconciliation | Runtime integration | B1, B3, B4, B5 | 2 days | installer idempotency and credential handling |
| B7 progress UI | Frontend | B3 | 1 day | stale polling and misleading terminal states |
| B8 recovery/redaction | Platform/QA | B6 | 1.5 days | partial install recovery and log leakage |
| B9 clean acceptance | QA/Release | B2-B8 | 1 day | environment-specific Docker behavior |

Critical path: `B0 -> B1/B2 -> B3/B4/B5 -> B6 -> B8 -> B9`. B7 may start after B3 using a
stubbed operation feed, but final UI acceptance waits for B6.

## B0: Freeze Contracts And Boundaries

**Changes**

- Add bootstrap operation enums and response models.
- Add a bootstrap reconciler port owned by `platform_config`.
- Record the Docker-socket isolation decision in an ADR or this spec's accepted decision section.
- Define redaction requirements and stable error codes.

**Files**

- `src/repomesh/modules/platform_config/`
- `src/repomesh/api/`
- `docs/development/two-stage-bootstrap-spec.md`

**Done when**

- contracts contain no Docker, subprocess, FastAPI, or installer imports;
- architecture boundary tests pass;
- response examples validate against the API models.

## B1: Persist Bootstrap Operations

**Changes**

- Add `platform.bootstrap_operations` migration.
- Implement create-or-get-active, claim, renew lease, transition, fail, retry, and latest reads.
- Add partial unique index for one active `configure_execution_plane` operation.
- Use database time for lease comparisons.

**Tests**

- first create and idempotent replay;
- concurrent claim returns one owner;
- expired lease reclamation;
- illegal state/phase transition rejection;
- retry increments one attempt;
- migration upgrade and downgrade on PostgreSQL.

**Done when**

- operation survives API and reconciler restart;
- no in-memory job registry is authoritative.

## B2: Start The Minimal Product Plane

**Changes**

- Let `start-platform.*` continue when AgentTeams is absent and no model key exists.
- Start PostgreSQL, API, nginx, and bootstrap service first.
- Set `agentteams_required=false` only for the minimal phase.
- Make API and web health independent from AgentTeams readiness during setup.
- Preserve selected ports in `.secrets/startup.env`.

**Tests**

- Docker-only launch with no model environment and no AgentTeams containers;
- login page and `/setup/status` reachable;
- AgentTeams/Matrix are system-owned missing states;
- rerun reuses ports and database volume.

**Done when**

- launcher exits successfully after opening the setup page, not after AgentTeams readiness.

## B3: Bootstrap API And Model-Save Trigger

**Changes**

- Add `GET /api/v1/setup/bootstrap`.
- Add `POST /api/v1/setup/bootstrap/retry`.
- On model credential save, ensure an active operation exists if execution plane is not ready.
- Merge bootstrap phase into dependency status projection.

**Tests**

- anonymous and non-admin rejection;
- model save creates exactly one operation;
- repeated model save reuses active operation;
- retry accepted only for retryable or expired state;
- responses contain no secret values.

**Done when**

- the HTTP request returns before installation and progress is durable.

## B4: Bootstrap Reconciler Image And Service

**Changes**

- Add a dedicated bootstrap Dockerfile/entrypoint.
- Include only the checked-in AgentTeams installer and required Docker client/runtime tools.
- Mount Docker socket only into bootstrap.
- Mount credential key and AgentTeams secret storage only where required.
- Add Compose labels identifying project and target API service.
- Implement lease polling and graceful shutdown.

**Tests**

- Compose config proves API/web have no Docker socket;
- bootstrap has no published port;
- reconciler with no operation performs no Docker mutation;
- two reconcilers cannot execute the same lease.

**Done when**

- service can claim a synthetic operation and report a dry-run phase without exposing a secret.

## B5: Runtime Configuration Loader

**Changes**

- Define `.secrets/platform-runtime.env` schema.
- Load runtime settings at API startup with explicit precedence.
- Write with temporary file and atomic rename.
- Constrain and test file permissions where supported.
- Select API restart target by Compose labels.

**Tests**

- runtime file overrides bootstrap defaults but not explicit emergency overrides;
- partial file is never observed;
- malformed file fails closed with a safe error;
- restart target refuses wrong project/service labels.

**Done when**

- a restarted API constructs Controller, Matrix, and storage adapters from the reconciled file.

## B6: Execution-Plane Reconciliation Workflow

**Changes**

- Read/decrypt the latest model connection in reconciler memory.
- Run AgentTeams installer with environment-based secret input, never argv.
- Skip install when Controller is healthy.
- Obtain Controller token, Matrix access token, and MinIO credentials.
- Write RepoMesh runtime config, restart API, verify all required dependencies.
- Mark operation complete only after verification.

**Tests**

- installer environment is redacted in logs;
- existing installation takes skip path;
- each phase is idempotent;
- completed operation corresponds to ready setup dependencies;
- submitted model key is absent from API responses, logs, and process arguments.

**Done when**

- no second launcher invocation is required after model save.

## B7: Setup Progress UI

**Changes**

- Add typed bootstrap API client.
- Poll progress only while operation is non-terminal.
- Show phase-specific automatic progress after model save.
- Show retry only for `retryable_failure`.
- Link `waiting_for_user` back to model form.
- Keep optional GitHub App and onboarding states separate.

**Tests**

- model save transitions to progress without page reload;
- refresh restores current phase;
- retry sends one request and disables while pending;
- terminal failure displays safe detail only;
- responsive layout has no overlap at desktop and mobile widths.

**Done when**

- the user never sees a command or internal token field.

## B8: Failure Recovery And Redaction

**Changes**

- Map installer/Docker/network errors to stable codes.
- Add bounded exponential retry for transient internal phases.
- Add lease expiry and recovery behavior.
- Add central secret redaction for bootstrap logs.
- Prevent retry of terminal safety failures.

**Tests**

- image pull failure;
- installer interruption;
- reconciler crash after install but before state transition;
- API restart failure;
- Matrix login failure;
- log corpus scan for model, Matrix, MinIO, and internal tokens.

**Done when**

- every failure gives either automatic retry, a retry button, or one precise user-owned action.

## B9: Clean-Machine Acceptance

**Windows P0 matrix**

- Docker Desktop running, no Python, uv, Node.js, npm, or AgentTeams;
- no model key in environment or database;
- occupied preferred DB/API/Web ports;
- browser closed during install and reopened;
- launcher and reconciler restarted mid-operation.

**Required evidence**

- launcher transcript with secrets redacted;
- Compose service/health snapshot;
- bootstrap state transition timeline;
- browser screenshots for waiting, running, completed, and retryable failure;
- proof that model key is absent from image history, container arguments, API responses, and logs;
- full Ruff, pytest, frontend lint/build, migration upgrade/downgrade results.

**Done when**

- only Docker and the user's model credential are required;
- one launcher invocation reaches a ready execution plane.

## Suggested Commits

1. `docs: specify two-stage execution-plane bootstrap`
2. `feat(platform): persist bootstrap operations`
3. `feat(startup): boot minimal product plane without model credentials`
4. `feat(bootstrap): add isolated execution-plane reconciler`
5. `feat(platform): load reconciled runtime configuration`
6. `feat(setup): trigger and expose bootstrap progress`
7. `feat(frontend): render automatic bootstrap progress and retry`
8. `test(startup): cover recovery redaction and clean-machine flow`
