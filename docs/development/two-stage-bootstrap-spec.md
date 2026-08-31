# Two-Stage AgentTeams Bootstrap Specification

Status: proposed for P0 implementation

Related: [Unified startup spec](unified-startup-spec.md)

## Problem

The AgentTeams installer consumes the model API key before a new operator can reach RepoMesh's
browser setup wizard. A product launcher that installs AgentTeams first therefore cannot support a
machine with no model credential. Supplying a placeholder key is not acceptable: it produces a
green installation that fails when the first real agent runs.

The product must boot a minimal control plane first, accept the model credential in the browser,
then install and configure the execution plane automatically.

## User Outcome

On a machine with Docker running and no host Python, Node.js, AgentTeams, or model credential:

1. the user runs `scripts/start.ps1` or `scripts/start.sh` once;
2. the login/setup page opens while AgentTeams is absent;
3. the user creates the first administrator and saves the model connection;
4. the page shows automatic installation progress;
5. AgentTeams, Matrix, MinIO, and the full RepoMesh execution plane become ready;
6. the user enters the console without running another command or copying a credential.

Closing or refreshing the browser does not cancel bootstrap. Rerunning the product launcher does
not create a second installation.

## Scope

### In scope

- minimal PostgreSQL/API/nginx startup without AgentTeams;
- durable bootstrap operation state and retry behavior;
- a purpose-built bootstrap reconciler;
- secure model credential consumption by the reconciler;
- non-interactive AgentTeams install and runtime credential discovery;
- RepoMesh API restart/reload after execution-plane configuration;
- setup status and progress UI;
- recovery from process restart, image-pull failure, and interrupted install.

### Out of scope

- silent Docker installation;
- arbitrary command execution from the browser;
- generic host package management;
- GitHub App creation or OAuth Manifest Flow;
- Coding Agent CLI login;
- replacing the browser action token with local-session authentication.

## Architecture Decision

Add a separate `bootstrap` service to the product Compose profile.

```text
browser
  │ local admin session
  ▼
RepoMesh API ── encrypted model credential ── PostgreSQL
  │ creates bootstrap operation                    ▲
  ▼                                                │ claim/update
bootstrap reconciler ── AgentTeams installer ──────┘
  │
  ├── Docker socket (bootstrap service only)
  ├── AgentTeams secret/runtime files
  └── restart selected RepoMesh API container
```

The RepoMesh API never receives the Docker socket. The browser never sends shell text, executable
paths, environment variables, or installer arguments. The reconciler implements one fixed
operation: `configure_execution_plane`.

For P0 the bootstrap service may mount the Docker socket because installation requires creating
networks, volumes, and containers. It must be isolated from normal API traffic, carry no published
port, and stop or idle after completion. A restricted Docker socket proxy is a later hardening item.

## Startup Phases

### Phase A: minimal control plane

The product launcher always starts:

- PostgreSQL and migrations;
- RepoMesh API with `agentteams_required=false` when AgentTeams is absent;
- nginx web console;
- generated internal RepoMesh credentials;
- bootstrap reconciler.

The launcher declares success when the login/setup page is reachable. AgentTeams readiness is not
a prerequisite for minimal-plane health.

### Phase B: execution-plane reconciliation

Saving a valid model connection creates or wakes a `configure_execution_plane` operation. The
reconciler:

1. claims the operation under a lease;
2. decrypts the model connection in memory;
3. runs the checked-in AgentTeams installer non-interactively;
4. verifies Controller health;
5. obtains Controller, Matrix, and MinIO runtime credentials;
6. writes a RepoMesh runtime configuration file without the model API key;
7. restarts the product API container selected by Compose labels;
8. verifies API, AgentTeams, Matrix, and storage readiness;
9. marks the operation complete.

The model key is persisted only in RepoMesh's encrypted credential table and AgentTeams' own
installer-managed secret storage. It is not copied into RepoMesh runtime configuration.

## Durable State

Add `platform.bootstrap_operations`:

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | Operation identity |
| `kind` | varchar(64) | `configure_execution_plane` only in P0 |
| `state` | varchar(32) | State enum below |
| `phase` | varchar(64) | Current phase enum below |
| `attempt` | integer | Incremented on each claim after retry |
| `requested_by` | UUID nullable FK | Local administrator |
| `lease_owner` | varchar(128) nullable | Reconciler instance |
| `lease_expires_at` | timestamptz nullable | Crash recovery |
| `error_code` | varchar(64) nullable | Stable safe code |
| `error_detail` | text nullable | Redacted operator-safe detail |
| `requested_at` | timestamptz | Initial request |
| `started_at` | timestamptz nullable | First claim |
| `updated_at` | timestamptz | Every transition |
| `finished_at` | timestamptz nullable | Terminal transition |

States:

- `pending`: requested, not claimed;
- `running`: lease held and a phase is executing;
- `waiting_for_user`: model credential was removed or is unusable;
- `retryable_failure`: safe to retry from the recorded phase;
- `terminal_failure`: automatic recovery is unsafe;
- `completed`: execution plane verified ready.

Phases:

- `waiting_for_model`;
- `installing_agentteams`;
- `verifying_controller`;
- `configuring_matrix`;
- `configuring_storage`;
- `writing_runtime_config`;
- `restarting_api`;
- `verifying_platform`;
- `complete`.

Only one non-terminal operation of this kind may exist. Enforce this with a PostgreSQL partial
unique index. Claims use `SELECT ... FOR UPDATE SKIP LOCKED` and a lease so a crashed reconciler can
be replaced safely.

## API Contract

### Read progress

`GET /api/v1/setup/bootstrap`

- authentication: local administrator session;
- response contains no credentials or raw installer output;
- returns `200` with the latest operation, or an idle projection when none exists.

```json
{
  "state": "running",
  "phase": "installing_agentteams",
  "attempt": 1,
  "retryable": false,
  "error_code": null,
  "message": "Installing the AgentTeams execution plane",
  "updated_at": "2026-08-27T03:00:00Z"
}
```

### Retry

`POST /api/v1/setup/bootstrap/retry`

- authentication: local administrator session;
- allowed only from `retryable_failure` or expired `running` lease;
- idempotently returns the active operation;
- never accepts a command, path, environment map, or credential body.

### Trigger

Successful `PUT /api/v1/setup/credentials/model` ensures a pending operation exists when the
execution plane is not ready. It does not wait for installation inside the request.

## Runtime Configuration

The reconciler writes `.secrets/platform-runtime.env` atomically with:

- Controller URL and token reference;
- Matrix URL and access token reference;
- MinIO endpoint, access key, secret key, and bucket;
- `REPOMESH_AGENTTEAMS_REQUIRED=true`.

The API startup path loads this file after ordinary environment settings. The file is mounted
read-only into the API container. Writes use a temporary file plus atomic rename; permissions are
restricted where the host filesystem supports it.

The reconciler locates the API container using both Compose project and service labels, never a
hard-coded container name. It restarts only that selected container and then waits for health.

## Idempotency And Recovery

- An already healthy Controller skips installation and proceeds to credential discovery.
- Installer reruns use its existing persisted environment and volumes.
- Every phase first checks its desired postcondition; completed work is not repeated.
- An expired lease makes `running` retryable.
- Browser refresh reads durable operation state; progress is not stored in process memory.
- API restart does not lose the operation because PostgreSQL owns its state.
- Removing the model credential moves reconciliation to `waiting_for_user`; it never reuses a
  deleted value.

## Error Taxonomy

Stable codes include:

- `docker_unavailable`;
- `model_credential_missing`;
- `model_credential_invalid`;
- `image_pull_failed`;
- `agentteams_install_failed`;
- `controller_unhealthy`;
- `matrix_login_failed`;
- `storage_credentials_missing`;
- `runtime_config_write_failed`;
- `api_restart_failed`;
- `platform_verification_failed`.

Raw stderr is retained only in local bootstrap-service logs with secret redaction. API responses
carry a bounded safe detail and a recommended retry/wait action.

## Setup Status Mapping

While bootstrap runs:

- `model`: `ready` after encrypted save;
- `agentteams`: `repairing` during install, `failed` on terminal failure;
- `matrix`: `repairing` after Controller health until login succeeds;
- `internal_auth`: remains `ready` because RepoMesh generated it in Phase A;
- `database`: remains `ready`;
- `repositories` and `agent_directory`: remain `pending_onboarding`.

`ready_for_project_creation` becomes true only after model, administrator, database, AgentTeams,
Matrix, and internal authentication are ready.

## Security Requirements

- No Docker socket on the API or web containers.
- Bootstrap API is admin-session protected and command-free.
- Model key never appears in argv, URLs, JSON responses, build args, or logs.
- Reconciler redacts submitted key, authorization headers, Matrix tokens, and MinIO secrets.
- Bootstrap container has no published port.
- Runtime configuration uses atomic writes and is gitignored.
- Operation error details are bounded and contain no subprocess environment dump.
- Container restart target is constrained by Compose labels and expected image/service identity.

## Acceptance Scenarios

1. No model key, no AgentTeams: login page opens and model is `waiting_for_user`.
2. Save model key: operation progresses to `completed` without another command.
3. Browser refresh mid-install: the same operation and phase remain visible.
4. Reconciler restart mid-install: expired lease is reclaimed, attempt increments once.
5. Image pull fails: state is `retryable_failure`; retry resumes safely.
6. Invalid model key: no green execution plane; user receives a credential correction action.
7. Existing healthy AgentTeams: installer is skipped and runtime credentials are reconciled.
8. API restart: account, encrypted credentials, operation, and selected ports persist.
9. Logs and image history contain none of the submitted model key or generated internal secrets.
10. Main API cannot access the Docker socket.

## Open Follow-Ups

- Replace direct Docker socket access with a restricted bootstrap proxy.
- Replace browser action-token authentication with local sessions.
- Add macOS and Linux clean-machine acceptance runners after Windows P0 passes.
