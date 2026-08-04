# RepoMesh Runner Worker Runtime v1

Status: Frozen 2026-08-04.

This document defines the `repomesh-runner` worker runtime: the contract an AgentTeams worker must
satisfy when its runtime is RepoMesh Runner. It binds the AgentTeams runtime control plane and the
RepoMesh Runner execution plane. Message shapes are defined by the schemas in this package; this
document defines the process, environment, lifecycle, and storage boundary around them.

## Entrypoint

The container runs the RepoMesh Runner process as PID 1. No OpenClaw, QwenPaw, or Hermes component
is started. No agentconfig-generated configuration tree is read.

## Environment contract

Environment variables fall into exactly three classes.

Consumed:

- the task source endpoint;
- the event delivery endpoint;
- the pass-through values of `repomesh.dev/*` labels;
- the object-storage endpoint and its credential references.

Ignored:

- Matrix credentials. The collaboration channel is a later milestone.
- OpenClaw-family variables.

Rejected:

- any permission-bearing variable. Permissions come only from the RunnerTask. Environment variables
  such as `AGENTTEAMS_YOLO` are never read as a permission source.

## Configuration tree

None. The controller's agentconfig generator emits an empty tree for this runtime.

## Heartbeat

A heartbeat is not required in Local deploy mode. Auto-sleep never fires when `spec.idleTimeout` is
absent, `lastActiveAt` is written from Matrix activity rather than from process liveness, and
`lastHeartbeat` participates in lifecycle decisions in Edge deploy mode only. Edge deploy mode is
unsupported for this runtime.

## Termination

SIGTERM leads to graceful shutdown: the Runner finishes its current work and emits an `interrupted`
terminal event. SIGKILL follows after the grace period. This is aligned with the process-group
semantics of driver supervision: group TERM, wait, then group KILL.

## Storage

Only the prefixes granted through `accessEntries` are accessed. The `agents/<name>/*` workspace-sync
mechanism is not used.

## Compatibility

Additive optional changes are backward compatible within v1. Anything else — removing a clause,
narrowing or widening the meaning of an existing one, or adding a new requirement a deployed worker
would fail — requires a new contract version.
