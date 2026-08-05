# RepoMesh Runner

RepoMesh Runner is the Python execution plane placed inside an AgentTeams-managed Worker. It turns
an immutable Runtime v1 task into a native coding-agent session and publishes ordered observations
back to the RepoMesh product control plane.

The implementation lives in `src/repomesh_runner` so the current Python toolchain can lint and test
it together with RepoMesh. This component does not import RepoMesh business modules or AgentTeams
Go internals. Its only cross-process data model is `contracts/runtime/v1`.

## Direction

```text
transport adapter -> runner application -> execution/event ports
                                      -> coding CLI adapter
```

The Runner never owns project, task, approval, or delivery state. It may cache an execution lease
and native session locally, but all durable results are emitted as idempotent events and immutable
artifact references.

## Current Boundary

Implemented now:

- task and result contracts;
- deterministic event identity and event ordering;
- execution and event-sink ports;
- accepted, completed, failed, interrupted, and input-required terminal mapping;
- behavior tests using in-memory ports.

Still required before an image can run:

- task transport and authentication;
- read-only Context Workspace and isolated repository worktree materialization;
- bridge to the existing Codex, Claude Code, Cursor, and other CLI adapters;
- native session checkpoint, interrupt, and resume storage;
- artifact upload, test/correction loop, and runtime telemetry;
- an AgentTeams Worker runtime definition.

## Worker Image

`Dockerfile` builds an image whose PID 1 is the Runner (`python -m repomesh_runner`), satisfying
`contracts/runtime/v1/worker-runtime.md`. It carries **no vendor CLI and no credentials**: the only
coding agent installed is the validation mock described below, so the image is safe to build and run
anywhere. Install a real CLI in a derived image when you need one.

The permission boundary of this image is the container — its user, filesystem, and network scope.
The protocol permission callbacks are cooperative and enforce the task's deny rules only because the
agent asks and honours the answer; they are not containment.

Build from the repository root, because the image installs `src/repomesh_runner`:

```bash
docker build -f components/repomesh-runner/Dockerfile -t repomesh-runner:dev .
```

The context is filtered by `components/repomesh-runner/Dockerfile.dockerignore`, which BuildKit
prefers over the repository root `.dockerignore` (that one excludes all of `components/`, including
the mock agent this image installs).

Contents: Python 3.13 slim, `git` (the executor collects changed files with `git status
--porcelain`), `httpx` and `repomesh_runner` installed with `--no-deps` so the RepoMesh application
dependencies stay out, the mock agent, a non-root `runner` user (uid 10001), and `/workspace` as the
working directory.

### Runtime environment variables

Authoritative list: `src/repomesh_runner/runtime_env.py`. The Runner **consumes** exactly these.

| Variable | Required | Meaning |
| --- | --- | --- |
| `REPOMESH_RUNNER_TASK_SOURCE_URL` | yes | long-poll endpoint the worker asks for the next RunnerTask |
| `REPOMESH_RUNNER_EVENT_SINK_URL` | yes | endpoint the worker posts RunnerEvents to |
| `REPOMESH_RUNNER_WORKSPACE_ROOT` | yes | existing directory that contains every task workspace; the image defaults it to `/workspace` |
| `REPOMESH_RUNNER_STATE_DIR` | no | idempotency ledger location; defaults to `<workspace root>/.runner-state` |
| `REPOMESH_RUNNER_POLL_TIMEOUT_SECONDS` | no | long-poll timeout, default `30` |
| `REPOMESH_LABEL_*` | no | pass-through worker labels; `REPOMESH_LABEL_TEAM_ID` becomes `repomesh.dev/team-id` |

The Runner **refuses to start** if any permission-bearing variable is present —
`AGENTTEAMS_YOLO`, `AGENTTEAMS_DANGEROUSLY_SKIP_PERMISSIONS`,
`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS`, `REPOMESH_RUNNER_ALLOWED_TOOLS`,
`REPOMESH_RUNNER_BYPASS_PERMISSIONS`, `REPOMESH_RUNNER_PERMISSION_MODE`. Permissions reach the Runner
only through the `permissions` block of a RunnerTask. Every other variable in the environment is
ignored.

## Mock Coding Agent

`mock/mock_coding_agent.py` is a standard-library script that speaks the stream-json dialect the
`claude-code` profile uses (`src/repomesh_runner/drivers/stream_json.py` is its specification). The
image installs it on PATH as **`repomesh-mock-agent`**, which is the binary name the `mock` driver
profile resolves (`src/repomesh_runner/profiles.py`). Send a task with `adapter_id: "mock"` to drive
it.

Scenario selection is by environment variable; the names mirror the repomesh-side
`MockScenario` vocabulary.

| `REPOMESH_MOCK_SCENARIO` | behavior | driver result |
| --- | --- | --- |
| `success` (default) | tool call, final text, non-error terminal frame | `succeeded` |
| `test_failed` | runs a verification tool, terminal frame with `subtype: test_failed` | `failed` |
| `failed` | terminal error frame plus stderr | `failed` |
| `timeout` | announces the session, then goes silent | `timeout` (idle watchdog) |
| `cancelled` | heartbeats forever, never ends on its own | `interrupted` when the run is cancelled |
| `interrupted` | opens a tool call and never closes it | `interrupted` when the run is cancelled |
| `question_required` | emits a `control_request` for `WebFetch` | `input_required` when the policy escalates; continues on allow, fails on deny |

Supporting variables: `REPOMESH_MOCK_SESSION_SEED` (the session id is
`mock-<scenario>-<sha256 prefix>` of scenario plus seed, so it is stable across runs) and
`REPOMESH_MOCK_STATE_DIR` (where a turn is persisted so `--resume <session id>` can recall it; an
unknown id fails loudly).

The mock never calls a model and never edits the workspace. Where a scenario cannot be represented
faithfully in this protocol — nothing in stream-json lets an agent *declare* "interrupted" or "input
required" — the script creates the process behavior that provokes the status instead of printing a
fake one; the module docstring states each of those limits. `tests/runner/test_mock_agent_executable.py`
proves the protocol correctness by driving the real `StreamJsonDriver` against a real subprocess.
