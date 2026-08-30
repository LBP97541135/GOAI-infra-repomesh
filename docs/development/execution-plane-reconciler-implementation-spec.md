# Execution-Plane Reconciler Implementation Spec

Status: implemented and accepted for B6

Parents:

- [Two-stage bootstrap specification](two-stage-bootstrap-spec.md)
- [Bootstrap reconciler service spec](bootstrap-reconciler-implementation-spec.md)
- [Runtime configuration loader spec](runtime-configuration-implementation-spec.md)

## B6 Outcome

A pending `configure_execution_plane` operation advances from encrypted model credentials to a
verified AgentTeams/Matrix/MinIO execution plane without a second launcher invocation. Existing
healthy AgentTeams installations take an idempotent skip path.

## Security Boundary

- Model API key is decrypted in bootstrap memory and passed only in the installer child-process
  environment. It is never an argv item.
- Matrix login JSON is written to curl stdin with `--data-binary @-`; the password is never argv.
- MinIO, Controller, Matrix, and model secrets are never logged or persisted in operation detail.
- Subprocess stderr is discarded from application logs. Failures map to stable codes and bounded
  predefined details.
- Commands use `create_subprocess_exec` argv arrays. No shell command string is accepted.
- Only bootstrap has Docker socket access.
- API restart target comes exclusively from the B5 label selector.

## Executor Dependencies

`AgentTeamsBootstrapExecutor` receives:

- encrypted `PostgresPlatformCredentialStore`;
- `BootstrapOperationStore` for running-phase transitions;
- fixed-argv `BootstrapCommandRunner`;
- `DockerComposeApiTargetSelector`;
- atomic runtime-config writer;
- API readiness verifier;
- fixed installer and AgentTeams environment paths.

It receives no HTTP body, arbitrary executable, arbitrary environment map, or caller-selected
container/service.

## Command Runner

The runner accepts:

- argv tuple;
- optional bounded stdin bytes;
- optional child environment additions from an executor-owned whitelist;
- timeout.

It returns exit code and bounded stdout bytes. Stderr is never returned to API/domain state. Timeout
kills and awaits the process. Output over the configured limit fails closed.

Only these executable families are used in B6:

- `docker info`, `docker exec`, `docker inspect`, `docker ps`, `docker restart`;
- checked-in `bash components/agentteams/install/agentteams-install.sh`.

## Workflow

1. **Installing AgentTeams**
   - read `model.api_key`, `model.base_url`, and `model.model_name`;
   - missing API key -> `waiting_for_user/model_credential_missing`;
   - verify Docker socket;
   - probe `agentteams-controller` health;
   - when healthy, skip installer;
   - otherwise run installer non-interactively with model values in child environment.

2. **Verifying Controller**
   - probe health again;
   - read Controller token with fixed `docker exec ... cat`;
   - empty token -> `controller_unhealthy`.

3. **Configuring Matrix**
   - read installer-managed admin username/password from bootstrap secret file;
   - POST login through controller-local curl with JSON stdin;
   - parse JSON with the standard library;
   - missing token -> `matrix_login_failed`.

4. **Configuring Storage**
   - read MinIO username/password from installer-managed secret file;
   - missing values -> `storage_credentials_missing`.

5. **Writing Runtime Config**
   - atomically write the B5 whitelist file with Controller, Matrix, and MinIO values;
   - set `REPOMESH_AGENTTEAMS_REQUIRED=true`.

6. **Restarting API**
   - select unique API by own Compose project and `service=api`;
   - execute fixed `docker restart <id>`;
   - no Compose recreate is required because API loads the bind-mounted runtime file on process
     start.

7. **Verifying Platform**
   - wait for `http://api:8000/health/ready` from the bootstrap network;
   - bounded retries and timeout;
   - success returns to reconciler, which marks `completed/complete`.

## Host Bind Path For Clean Install

The shell AgentTeams installer drives the host Docker daemon, so bind sources it passes must be host
paths, not bootstrap-container paths. The executor inspects its own `/app/.secrets` mount source,
takes the parent as RepoMesh host root, and derives:

- `<host-root>/.agentteams/manager-workspace`;
- `<host-root>/.agentteams/host-share`;
- `<host-root>/.secrets/agentteams-manager.env`.

Windows Docker Desktop sources such as `C:\path` are normalized to `/host_mnt/c/path` before the
Linux installer invokes Docker. Paths containing newline or NUL are rejected.

## Runtime Environment Cutover

The product Compose API no longer hardcodes AgentTeams runtime variables. The startup script writes
`platform-runtime.env` when it discovers an existing healthy AgentTeams installation. Minimal mode
removes a stale runtime file before starting.

This ensures precedence works after `docker restart`: explicit emergency process environment may
still override, otherwise the runtime file controls AgentTeams settings.

## Error Mapping

| Failure | State | Code |
| --- | --- | --- |
| Model missing | waiting_for_user | model_credential_missing |
| Docker unavailable | retryable_failure | docker_unavailable |
| Installer nonzero/timeout | retryable_failure | agentteams_install_failed |
| Controller unhealthy/token empty | retryable_failure | controller_unhealthy |
| Matrix login failure | retryable_failure | matrix_login_failed |
| MinIO credentials missing | retryable_failure | storage_credentials_missing |
| Runtime write failure | retryable_failure | runtime_config_write_failed |
| Target/restart failure | retryable_failure | api_restart_failed |
| API never healthy | retryable_failure | platform_verification_failed |

No mapped detail includes subprocess output or a credential.

## Tests

- fake runner drives every phase and asserts exact argv shape;
- model key appears only in child environment;
- Matrix password appears only in stdin;
- healthy Controller skips installer;
- every failure maps to its stable code and state;
- runtime writer receives the exact whitelist and no model key;
- selector result is the only restart target;
- API verifier must pass before completion;
- log capture contains none of four sentinel secrets;
- current real AgentTeams environment completes a synthetic isolated operation through skip path;
- API reloads generated runtime config and remains healthy.

## Done

B6 is complete when production mode is enabled, the existing-AgentTeams skip path completes in an
isolated operation, no secret appears in logs/argv/API state, and current product services remain
healthy after the reconciler-controlled API restart. Clean first installation remains B9 evidence.
