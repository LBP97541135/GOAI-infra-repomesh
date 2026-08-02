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
- a container entry point and AgentTeams Worker runtime definition.
