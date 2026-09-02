# Runtime Configuration Loader Implementation Spec

Status: implemented and accepted for B5

Parents:

- [Two-stage bootstrap specification](two-stage-bootstrap-spec.md)
- [Two-stage bootstrap tasks](two-stage-bootstrap-tasks.md)

## B5 Boundary

B5 defines, validates, writes, and loads the reconciled RepoMesh execution-plane configuration. It
also identifies the unique API container that B6 may restart. B5 does not discover AgentTeams
credentials, execute the installer, restart a container, or verify the completed platform.

## Runtime File

Path: `.secrets/platform-runtime.env` on the host and `/app/.secrets/platform-runtime.env` in the
API/bootstrap containers.

Allowed keys only:

- `REPOMESH_AGENTTEAMS_REQUIRED`;
- `REPOMESH_AGENTTEAMS_CONTROLLER_URL`;
- `REPOMESH_AGENTTEAMS_CONTROLLER_TOKEN`;
- `REPOMESH_AGENTTEAMS_MATRIX_URL`;
- `REPOMESH_AGENTTEAMS_MATRIX_ACCESS_TOKEN`;
- `REPOMESH_AGENTTEAMS_STORAGE_ENDPOINT`;
- `REPOMESH_AGENTTEAMS_STORAGE_ACCESS_KEY`;
- `REPOMESH_AGENTTEAMS_STORAGE_SECRET_KEY`;
- `REPOMESH_AGENTTEAMS_STORAGE_BUCKET`.

The model connection is not allowed in this file. RepoMesh reads it from the encrypted credential
table; AgentTeams owns its own installer-managed model secret.

## Parsing

- UTF-8 text, one `KEY=value` per line;
- blank lines and lines beginning with `#` are ignored;
- values are literal text after the first `=`; quoting and shell expansion are not supported;
- unknown keys, duplicate keys, malformed assignments, NUL, CR, and embedded LF fail closed;
- `REPOMESH_AGENTTEAMS_REQUIRED` must be `true` or `false`;
- required URLs must use `http` or `https` when present;
- no parser error includes a value.

## Precedence

```text
explicit non-empty process environment
  > platform-runtime.env
  > .env
  > Settings defaults
```

The loader sets only keys that are absent or empty in `os.environ`. It runs before `Settings()` is
constructed in the production API entrypoint. The test/application factory does not implicitly
load a workstation runtime file.

`get_settings.cache_clear()` remains necessary after an in-process environment change, although
the production reconciliation path restarts the API.

## Atomic Writer

The bootstrap service writes in the target directory:

1. validate every key and value;
2. create a uniquely named temporary file;
3. set mode `0600` where supported;
4. write sorted assignments, flush, and `fsync`;
5. atomically replace `platform-runtime.env` with `os.replace`;
6. remove a leftover temporary file on failure.

Readers observe either the previous complete file or the next complete file. The writer never logs
the file body or values.

## Encryption Key Bootstrap

Product launchers generate `.secrets/platform-credentials.key` before Compose starts when neither
the file nor `REPOMESH_CREDENTIALS_ENCRYPTION_KEY` exists. The generated value is a valid padded
URL-safe Base64 Fernet key.

After this change the platform API mounts `.secrets` read-only. The bootstrap service retains the
only read-write mount. The compatibility `console-api` path remains read-write until its launcher
is migrated.

## API Startup Integration

`repomesh.main` loads runtime environment before importing/constructing the application. A malformed
existing runtime file prevents API startup and produces a value-free configuration error. A missing
file means minimal mode and is not an error.

The Docker image health check therefore cannot become healthy with a malformed runtime file.

## Compose Target Selection

The bootstrap container identifies its own Compose project with:

```text
docker inspect <self container id>
  label com.docker.compose.project
```

It selects the API using both labels:

- `com.docker.compose.project=<own project>`;
- `com.docker.compose.service=api`.

Selection rules:

- service name is fixed to `api`, not caller-provided;
- zero matches is `api_restart_failed`;
- multiple matches is `api_restart_failed`;
- selected container must repeat the expected project/service labels on inspect;
- only the container id is returned to B6;
- B5 issues no `docker restart` command.

Docker commands are passed as argv arrays without shell interpolation. Command failures retain only
a stable safe detail, not stdout/stderr bodies.

## Tests

### Runtime config

- valid round trip for all keys;
- explicit environment wins;
- runtime file beats Settings `.env`/defaults;
- unknown, duplicate, malformed, NUL, and invalid boolean fail without value disclosure;
- atomic replace preserves old file when replace fails;
- resulting mode is `0600` on POSIX;
- model credential keys are rejected.

### Target selector

- exact project/service match returns one id;
- zero/multiple matches fail;
- mismatched inspect labels fail;
- service cannot be changed by input;
- argv contains no shell command string.

### Compose/runtime

- API secret mount is read-only;
- bootstrap secret mount is read-write;
- only bootstrap has Docker socket;
- launchers generate a Fernet key before Compose;
- restarted API constructs AgentTeams adapters from a test runtime file.

## Done

B5 is complete when unit and Compose tests pass, the current API starts with its read-only secret
mount, a controlled test runtime file changes the API's effective AgentTeams settings after restart,
and no container restart capability has been added to the selector.
