# Bootstrap Reconciler Service Implementation Spec

Status: implemented and accepted for B4

Parent documents:

- [Two-stage bootstrap specification](two-stage-bootstrap-spec.md)
- [Two-stage bootstrap tasks](two-stage-bootstrap-tasks.md)

## B4 Boundary

B4 delivers the isolated service process, lease runner, Docker image, Compose security boundary,
and dry-run acceptance. It does not install AgentTeams, read model credentials, write runtime
configuration, or restart RepoMesh. Those effects belong to B5/B6.

Shipping a half executor is more dangerous than shipping no executor: B4 therefore shipped in
`disabled` mode. B6 now supplies the production executor and changes the product default to
`production`; `disabled` remains an emergency override.

## Process Contract

Entrypoint: `python -m repomesh.bootstrap_worker`.

Environment:

| Variable | Default | Rule |
| --- | --- | --- |
| `REPOMESH_BOOTSTRAP_MODE` | `disabled` | `disabled` or `dry-run` in B4 |
| `REPOMESH_BOOTSTRAP_POLL_SECONDS` | `2` | 1-60 seconds |
| `REPOMESH_BOOTSTRAP_LEASE_SECONDS` | `300` | 30-3600 seconds |
| `REPOMESH_BOOTSTRAP_INSTANCE_ID` | hostname | 1-128 characters |
| `REPOMESH_BOOTSTRAP_ONCE` | `false` | Test-only one-claim execution in dry-run mode |
| `REPOMESH_DATABASE_URL` | normal setting | Same PostgreSQL as API |

Modes:

- `disabled`: initialize database connectivity, expose container health, never call `claim`;
- `dry-run`: claim one operation at a time, execute no Docker or secret action, and mark the
  synthetic operation complete. This mode exists only for automated B4 acceptance.

Unknown modes fail before the health marker is written.

## Reconciler Application Service

`BootstrapReconciler` depends only on:

- `BootstrapOperationStore`;
- `BootstrapExecutor` port;
- instance id, poll interval, and lease duration.

`run_once()`:

1. claim one pending or expired operation;
2. return `False` immediately when none exists;
3. run the executor under a lease-renewal heartbeat;
4. cancel and await the heartbeat before the terminal transition;
5. mark success `completed/complete`;
6. map declared retryable/terminal execution errors to durable safe states;
7. map unexpected exceptions to a generic retryable failure without persisting exception text.

`run_forever()` waits through an interruptible stop event rather than an uninterruptible sleep.
SIGTERM and SIGINT request graceful shutdown. A running executor is allowed to finish its current
bounded action; the Docker stop grace period must exceed that bound once B6 defines it.

## Executor Port

```python
class BootstrapExecutor(Protocol):
    async def execute(
        self,
        operation: BootstrapOperation,
        lease_owner: str,
    ) -> None: ...
```

The executor never receives HTTP request data. B6 may depend on the operation store to advance
running phases, but it cannot alter operation identity, kind, requester, or attempt.

Declared failures use `BootstrapExecutionError` with:

- stable `BootstrapErrorCode`;
- bounded, already-redacted safe detail;
- retryable boolean.

The reconciler never persists `repr(error)`, subprocess output, environment contents, or traceback.

## Lease Heartbeat

Heartbeat interval is `min(lease_seconds / 3, 30 seconds)`, with a lower bound of one second.
Renewal uses the same lease owner. Losing ownership aborts the executor task and leaves the
operation reclaimable after lease expiry. B4 tests normal renewal; forced cancellation semantics
are completed alongside the bounded B6 executor.

## Image

File: `Dockerfile.bootstrap`.

Base: Python 3.12 Alpine with only:

- RepoMesh package;
- `docker-cli`;
- `bash`, `curl`, `openssl`, and standard POSIX tools required by the checked-in installer;
- checked-in `components/agentteams/install` files.

The image has no web server and exposes no port. It runs as a dedicated container process. Docker
socket access means root inside this container is effectively privileged on the Docker host; this
is accepted only for the isolated bootstrap service in P0 and is explicitly denied to API/Web.

## Compose Contract

Service name: `bootstrap`, profile: `platform`.

Required mounts:

- `/var/run/docker.sock:/var/run/docker.sock` only on `bootstrap`;
- `.secrets:/app/.secrets` for encryption key and future runtime config;
- AgentTeams installer is copied into the image, not mounted from an arbitrary host path.

Other constraints:

- no `ports` entry;
- default `REPOMESH_BOOTSTRAP_MODE=disabled` for B4;
- depends on healthy PostgreSQL;
- restart policy `unless-stopped`;
- health check reads a local readiness marker written after startup validation;
- API and Web service mounts must contain no Docker socket.

## Logging

Allowed fields:

- instance id;
- operation id;
- state and phase;
- attempt;
- stable error code.

Forbidden fields:

- model, Matrix, MinIO, Controller, session, or internal token values;
- full environment maps;
- subprocess argv once B6 can include sensitive file paths;
- raw response bodies or installer output.

## Tests

### Unit

- no operation returns `False` and does not call executor;
- one operation is claimed and dry-run completes it;
- two reconcilers produce one executor call;
- declared retryable and terminal errors map correctly;
- unexpected error stores generic detail only;
- heartbeat renews a short lease;
- stop event exits idle loop promptly.

### Compose security

- bootstrap has Docker socket; API/Web do not;
- bootstrap has no published port;
- default mode is disabled;
- bootstrap depends on PostgreSQL health;
- installer source is copied at build time.

### Container smoke

- disabled container becomes healthy and does not claim a pending synthetic operation;
- dry-run container claims and completes exactly one synthetic operation;
- image and logs contain no test sentinel secret.

## Done

B4 is complete when the unit and Compose security suites pass and a disabled container becomes
healthy without mutating a pending operation. Dry-run completion is tested only against an isolated
database. Production mode remains unavailable until B6.
