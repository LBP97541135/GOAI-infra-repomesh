# RepoMesh Room-Native Agent Bridge

The Bridge is the Python process an operator runs on their own machine to give one AgentTeams
external Worker (`containerManaged: false`) a local coding CLI. It is the only component that lives
outside the cluster on purpose: the CLI it drives is installed on that machine, licensed to that
operator, and never shipped into an image.

The implementation lives in `src/repomesh_agent_bridge` so the repository's toolchain lints and
tests it with everything else. It imports no RepoMesh business module and no AgentTeams Go
internals. Its only cross-process data models are `contracts/agent-bridge/v1`, and RepoMesh is the
only control plane it talks to — it holds no AgentTeams management credential and never calls the
Go controller.

Adopted by `docs/adr/0004-room-native-agent-bridge.md`.

## Direction

```text
enrollment file -> local validation -> instance claim -> RepoMesh preflight -> Matrix -> coding CLI
```

The order is the contract, not a suggestion. Anything decidable locally is decided before a socket
is opened; binding and room ownership are always confirmed against RepoMesh's live state; Matrix
sync and any CLI process come strictly after that confirmation.

## Current Boundary

Implemented now:

- the `repomesh.agent-bridge.enrollment.v1`, `.binding.v1` and `repomesh.room-observation.v1` wire
  models, with schema validation at the wire boundary;
- two-stage startup validation and its refusal taxonomy;
- the RepoMesh preflight HTTP adapter, with a bounded retry policy;
- a per-worker instance claim that makes a second Bridge for the same worker fail fast;
- the process lifecycle: start, stay up, and unwind cleanly on cancellation.

Not implemented yet, and not claimed anywhere:

- Matrix sync, mention detection, inbound dedup and restart recovery (PR 3);
- a real coding CLI session, which may only ever be launched through a restricted `ProcessFactory`
  (PR 4);
- governed execution against RepoMesh Tasks (PR 5).

Until PR 3 and PR 4 land, the room and coding-session seams are inert stand-ins: the process
validates, claims its worker, confirms its binding, and then idles. That is the whole of this
tier, and the `check` subcommand says so in its own output.

## Enrollment

One JSON document per Bridge instance, and it holds no secrets — credential fields are opaque
references resolved locally at startup:

```json
{
  "schemaVersion": "repomesh.agent-bridge.enrollment.v1",
  "organizationId": "00000000-0000-0000-0000-000000000001",
  "workerAgentId": "00000000-0000-0000-0000-000000000002",
  "workerName": "pricing-codex-worker",
  "teamName": "pricing-repo-team",
  "matrixUserId": "@pricing-codex-worker:matrix.example.org",
  "matrixHomeserverUrl": "https://matrix.example.org",
  "allowedRoomIds": ["!team-pricing:matrix.example.org"],
  "repomeshEndpoint": "https://repomesh.example.org",
  "codingProfile": "codex",
  "credentialRefs": {
    "matrix": "env:REPOMESH_BRIDGE_MATRIX_TOKEN",
    "repomesh": "env:REPOMESH_BRIDGE_TOKEN"
  }
}
```

`codingProfile` must be one of `codex`, `claude-code`, `kimi` — a subset of the Runner's profiles,
because the Runner also carries a validation-only `mock` profile that must never serve real work.

This tier's credential resolver understands one locator form, `env:NAME`; a reference it cannot
resolve is refused during local validation, before any network call. Resolved values go into the
request that needs them and nowhere else — never into a log line, stdout, or an error message.

`credentialRefs.repomesh` is required whenever preflight is authenticated, which it is against a
real RepoMesh: preflight currently authenticates with the runner control token, the same credential
the Bridge already holds as its worker's Runner consumer. A Worker-scoped credential is PR 5's
subject.

## Running

```bash
# Validate everything that can be validated, print what RepoMesh confirmed, and exit.
repomesh-agent-bridge check --enrollment ./enrollment.json

# Serve the worker. Blocks until interrupted.
repomesh-agent-bridge run --enrollment ./enrollment.json
```

`python -m repomesh_agent_bridge` is the same entry point.

`check` joins no room and spawns no process, and it deliberately does not take the instance claim,
so it can be run against a worker that is already being served. Exit codes: `0` success, `2` a
startup refusal at either stage (and argparse's own usage errors), `3` another instance already
serves this worker.

## State and the instance claim

Exactly one Bridge may serve a given worker at a time; a second one would answer the same mentions
and lease the same tasks. The claim is an OS lock held on an open file handle for as long as `run`
is alive, so the operating system releases it if the process dies — there is no stale sentinel to
clean up.

The lock lives under a per-user state directory, never in a repository:

| Platform | Default |
| --- | --- |
| Windows | `%LOCALAPPDATA%\repomesh-agent-bridge\locks\<workerAgentId>.lock` |
| Other | `~/.local/state/repomesh-agent-bridge/locks/<workerAgentId>.lock` |

`run --state-dir <dir>` overrides the base directory, which is what the test suite uses.

## Isolation

This tier spawns no CLI process at all, so it makes no isolation claim. When PR 4 introduces one,
a real CLI may only be launched through a restricted `ProcessFactory` (restricted OS identity/ACL,
an environment-variable allowlist, and a dedicated workspace); the CLI's own permission callback is
a cooperative second line of defence, not the boundary. On a platform with no verified
restricted-launch adapter, only fake/scripted sessions are permitted — see ADR 0004 decision 9 and
the contract's "Isolation" section.

## Liveness

A local health signal only: readiness after startup, reported to whatever supervises the process on
that machine. There is no platform heartbeat and no AgentTeams/RepoMesh "online" display wired to
Bridge liveness in this tier, because no component receives one.

## Packaging

The Bridge ships from the repository's own distribution and installs the `repomesh-agent-bridge`
console script:

```bash
python -m build
pip install dist/repomesh-0.1.0-py3-none-any.whl
```
