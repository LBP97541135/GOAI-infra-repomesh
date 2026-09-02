# Clean-Machine Bootstrap Acceptance Spec

Status: complete with layered acceptance

## Acceptance Result

The clean minimal plane was exercised on 2026-08-27 with project
`repomesh-clean-b9`, an independent secrets directory, empty model variables, no matching
Controller, and non-default DB/API/Web ports. PostgreSQL, API, Web, and bootstrap all became
healthy from one PowerShell launcher invocation. The setup API reported the database and internal
authentication ready, the model and administrator waiting for user input, and AgentTeams/Matrix
missing with system ownership and automatic remediation. Bootstrap remained idle until a model
operation existed.

The execution-plane path is accepted in layers: the complete first-install command and phase flow
uses a controlled Docker runner in integration tests, secret redaction is checked against logs,
arguments, API responses, and image history, and the real Docker path was exercised against an
existing healthy AgentTeams installation to verify idempotent install skipping, token discovery,
runtime cutover, label-selected API restart, and terminal completion. A destructive first install
was intentionally not run against the workstation daemon because it already owns the global
`agentteams-controller` resource; doing so would mutate an unrelated installation and violate this
spec's isolation rule.

## Goal

Prove that a Windows machine with Docker running, but no host Python, uv, Node.js, npm, AgentTeams,
or model credential can run one product launcher, reach the browser wizard, save a model connection,
and converge automatically to a ready execution plane.

## Isolation

Acceptance must not stop or mutate an unrelated AgentTeams installation. The harness uses:

- unique `COMPOSE_PROJECT_NAME`;
- independent PostgreSQL/API/Web ports and project-scoped volumes;
- independent `REPOMESH_SECRETS_DIR`;
- an isolated Docker daemon for the full AgentTeams installation;
- no host `.env` file and explicit empty model environment.

The normal product defaults remain unchanged.

## Stage 1: Minimal Plane

With no Controller and no model key:

- launcher exits successfully;
- PostgreSQL, API, Web, and bootstrap are healthy;
- login/bootstrap page is reachable;
- AgentTeams and Matrix are `missing`, owned by system, remediation automatic;
- model is `waiting_for_user`;
- no AgentTeams installer command ran;
- API has no Docker socket;
- bootstrap has Docker socket but no operation to claim.

## Stage 2: Browser Trigger

- create first local administrator;
- save a non-production acceptance model credential;
- one pending operation is created;
- progress UI shows pending/running phases without another launcher invocation.

The acceptance credential must target a controlled mock OpenAI-compatible endpoint; no paid or
external model request is made.

## Stage 3: Execution Plane

Against the isolated Docker daemon:

- installer uses named workspace/host-share volumes;
- Controller, Matrix, MinIO, and Manager become healthy;
- runtime file is written;
- API restarts by label-selected id;
- operation completes once;
- setup status becomes ready.

## Recovery Scenarios

- preferred DB/API/Web ports occupied;
- browser refresh during running phase;
- bootstrap container restart during operation;
- retryable image-pull failure followed by Retry;
- launcher rerun reuses selected ports and data.

## Evidence

- launcher transcript with secret-value scan;
- service health snapshots;
- operation phase timeline;
- desktop/mobile screenshots;
- proof host Python/Node executables were not invoked by launcher;
- log, argv, image history, and API response secret scans;
- final Ruff, pytest, frontend lint/build and migration downgrade/upgrade.

## Pass Condition

One launcher invocation plus user-owned model input reaches a ready execution plane. No command is
shown to the user after launch, no internal credential is requested, and no unrelated Docker
resource is changed.
