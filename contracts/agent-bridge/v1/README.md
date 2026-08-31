# Agent Bridge Contracts

Agent Bridge contracts bind the Room-Native Coding Agent Bridge (an externally hosted process on
an operator machine) to the AgentTeams runtime control plane, Matrix rooms, and the RepoMesh
product control plane. Adopted by ADR 0004. New incompatible shapes use a new sibling version
directory; deployed consumers must never infer fields that are absent from their declared
version.

Current version: `v1`. The three documents below share the `v1` era but each carries its own
unified schema version string, and a consumer must check the specific string it received, not
just the directory version:

| Document | `schemaVersion` |
|---|---|
| `external-worker-enrollment.schema.json` | `repomesh.agent-bridge.enrollment.v1` |
| `external-worker-binding.schema.json` | `repomesh.agent-bridge.binding.v1` |
| `room-observation.schema.json` | `repomesh.room-observation.v1` |

## Documents

- `external-worker-enrollment.schema.json` — the non-secret binding a Bridge instance needs to
  serve one AgentTeams `containerManaged: false` Worker. Credential fields are opaque
  references; secret values never appear in an enrollment, a room message, a task, or a log.
- `external-worker-binding.schema.json` — the versioned response RepoMesh preflight returns to a
  Bridge instance during startup, after local enrollment validation and before Matrix sync or
  any CLI process is spawned. It is the network-authoritative confirmation of worker binding and
  room ownership. The Bridge never holds AgentTeams management credentials and never queries the
  AgentTeams Go Controller directly; RepoMesh is the only control plane it talks to for these
  facts.
- `room-observation.schema.json` — the only shape the Bridge may project into a room. Matrix
  text is a display projection of this document; structured truth stays in RepoMesh.

## Interface semantics

These facts are part of the Bridge interface. Implementations and tests must uphold them; they
are not implementation details.

### Validation and startup

Startup validation is two stages, and the boundary matters: stage 1 never touches the network,
stage 2 always does.

1. **Local validation (pre-network).** The enrollment document is checked against
   `external-worker-enrollment.schema.json`, the named `codingProfile` is a known Runner
   profile, and every `credentialRefs` entry resolves to a non-empty opaque reference. This
   stage runs before any socket is opened — including to RepoMesh — so malformed local
   configuration is rejected for free, without a network round-trip.
2. **RepoMesh preflight (post-network, pre-Matrix-sync, pre-CLI-spawn).** Once stage 1 passes,
   the Bridge calls RepoMesh preflight through `WorkerBindingPort` and receives a versioned
   `repomesh.agent-bridge.binding.v1` response (`external-worker-binding.schema.json`). This is
   the only place `containerManaged: false`, Worker binding, and room-ownership authority are
   checked — against RepoMesh's live state, never merely restated from the enrollment document.
   Preflight must succeed strictly before Matrix sync starts and before any CLI process is
   spawned.

A failure at either stage is fail-fast and aborts startup the same way: an enrollment that does
not validate against its schema, names an unknown coding profile, references a Worker that
RepoMesh preflight does not confirm as `containerManaged: false`, or names rooms RepoMesh does
not confirm as authoritatively allowed, is rejected before Matrix sync or CLI startup.

The Bridge announces readiness only after Matrix sync is established and local state is
recovered. AgentTeams must not fabricate container readiness for external workers — see
"Liveness" below for what the Bridge itself reports.

### Trust model

- Matrix room messages are a wake-up and display channel only. A mention starts a turn; a room
  message never itself grants a permission, assigns a Task, or ends one.
- Task existence, assignee identity, permission scope (allowed/denied paths and tools), and
  terminal run state are authoritative only in RepoMesh's persisted state. The Bridge treats
  Matrix content as untrusted input to be validated against RepoMesh at every governed-execution
  boundary, never as a fact it can act on by itself.
- A participant declaring "done" or "approved" in a room has no effect on Task state. Only a
  persisted Runner terminal event, or an explicit RepoMesh-recorded decision (for example an
  authorized `input_required` answer), changes what RepoMesh considers true.

### Idempotency

Three stable keys, one per layer:

| Layer | Identity |
|---|---|
| Matrix inbound | room id + event id |
| CLI conversation turn | worker + native session id + trigger event id |
| Governed execution | Runtime v1 `idempotencyKey` + event sequence |

- A redelivered or duplicated Matrix event produces at most one CLI turn and at most one
  outbound reply.
- Outbound room messages use deterministic Matrix transaction ids so a crash between send and
  acknowledge does not produce duplicate room messages on restart.

### Recovery

- On restart the Bridge resumes from its persisted Matrix cursor, inbound ledger, and active
  session references. It never re-derives pending work by re-reading room timelines.
- First sync after enrollment establishes a baseline; historical messages before the baseline
  are never executed.

### Scope and authority

- Only events from rooms in the RepoMesh-confirmed `allowedRoomIds` (the intersection of the
  enrollment's list and the `external-worker-binding.v1` response) are processed; invites
  outside the allowlist are not auto-joined.
- Only messages that mention the enrolled worker's Matrix user id start a turn.
- Conversation sessions run with a deny-all tool permission policy and a throwaway workspace.
  Repository writes require the governed execution path (approved Task, assignee match,
  platform-prepared worktree, Runtime v1 permissions); no room message can widen paths, tools,
  network targets, or credentials.
- Room text never advances Task state. Only persisted Runner events do.

### Isolation

This tier's isolation is layered and cooperative, not a sandbox guarantee:

1. Restricted OS identity/ACL — the CLI process runs under a restricted local OS identity (or
   an equivalent ACL-scoped account), never the operator's own account.
2. Environment-variable allowlist — the process receives only an explicit allowlist of
   environment variables; ambient secrets and the operator's full environment are not
   inherited.
3. Dedicated workspace — the CLI runs inside a Bridge/Run-scoped workspace directory, never the
   operator's home directory or an arbitrary path.
4. Protocol permission callback — the coding CLI's own tool/write permission callback (deny-all
   in the conversation track) is a second line of defense on top of 1–3, not the isolation
   boundary itself; it is cooperative because it depends on the CLI process honoring its own
   protocol.

A real CLI may only be spawned through a restricted `ProcessFactory` implementing 1–3. When no
verified restricted-launch adapter exists for the host platform, only fake/scripted coding
sessions are permitted, and no document in this contract set may claim the Bridge has "no write
access" — that claim holds only once a verified adapter exists for the platform in question.

### Liveness

This tier ships a local health probe only: the Bridge can report its own process health to
whatever runs it locally (for example a process supervisor). There is no platform heartbeat and
no AgentTeams/RepoMesh "online" display wired to Bridge liveness in this tier — a remote
heartbeat with no receiving component is not a contract this document makes. Platform-visible
online status is a later tier's decision, made only once a receiving component exists.

### Lifecycle

- `run` blocks until cancelled. Cancellation terminates the entire CLI process group the Bridge
  started, persists state, and leaves any in-flight governed run to Runtime v1 interruption
  semantics.

## Invariant → acceptance mapping

Every startup invariant above names the future PR/test that verifies it automatically; none of
them is verified by this document alone.

| Invariant | Verified by |
|---|---|
| Stage-1 local schema/profile/credential-ref validation runs before any network call | PR 2 — local fail-fast + preflight ordering tests |
| Stage-2 RepoMesh preflight (`containerManaged`, worker binding, room ownership) runs after the network is available but strictly before Matrix sync and CLI spawn | PR 2 — local fail-fast + preflight ordering tests |
| A redelivered/duplicated Matrix event produces at most one CLI turn and at most one outbound reply | PR 3 — dedup/recovery tests |
| Deterministic outbound transaction ids prevent duplicate room messages across a crash/restart | PR 3 — dedup/recovery tests |
| Restart resumes from persisted cursor/ledger/session references without re-deriving work from room timelines | PR 3 — dedup/recovery tests |
| Restricted `ProcessFactory` (OS identity/ACL, env allowlist, dedicated workspace) is the real isolation boundary, not the protocol permission callback | PR 4 — isolation probe |
| Only events from RepoMesh-confirmed `allowedRoomIds` are processed; invites outside the allowlist are not auto-joined | PR 4 — isolation probe |
| Governed execution requires an approved Task, assignee match, and a platform-prepared worktree; no room message can widen paths, tools, network targets, or credentials | PR 5 — governed execution auth |
| Room text never advances Task state; only persisted Runner events do | PR 5 — governed execution auth |
