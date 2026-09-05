# Runtime Contracts

Runtime contracts connect the RepoMesh product control plane, AgentTeams runtime control plane,
and RepoMesh Runner execution plane. New incompatible shapes use a new sibling version directory;
deployed consumers must never infer fields that are absent from their declared version.

Current version: `v1`.

## Leasing over HTTP (`GET /api/v1/runtime/runner-tasks/next`)

The envelope is the v1 `RunnerTask`. The transport rules below are part of the contract because
three kinds of consumer share one dispatch table: the managed Runner of the one-shot stack, the
out-of-cluster Bridges, and the hosted-native verifier.

- `wait=<seconds>`: the long-poll window. `200` carries one task envelope, `204` means nothing
  runnable for this caller.
- `adapter=<id>` (repeatable, or comma separated): the adapters the caller can run. Only dispatches
  whose `adapterId` is listed are leased to it.
- A **worker credential** (`REPOMESH_RUNNER_WORKER_TOKENS`) names one worker and leases only that
  worker's queue. `workerAgentId` may repeat that id and never name another (`403`); `adapter` is
  optional and only narrows further.
- A **subjectless credential** (the global `REPOMESH_RUNNER_CONTROL_TOKEN`) leases across queues,
  so it **must** send `adapter` (`400` without one) and is never handed a dispatch for a worker
  that holds a credential of its own: that queue is that worker's Bridge's (`403` when named with
  `workerAgentId`, skipped otherwise). The decision is taken from the deployment's credential map,
  not from a control-plane read per poll.
- In force since 2026-09-04. A consumer that polled with the control token and no `adapter` must
  add it; `repomesh-runner` sends the launchable profiles of its host by default and
  `REPOMESH_RUNNER_ADAPTERS` overrides that list.
