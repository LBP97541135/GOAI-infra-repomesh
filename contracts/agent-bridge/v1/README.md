# Agent Bridge Contracts

Agent Bridge contracts bind the Room-Native Coding Agent Bridge (an externally hosted process on
an operator machine) to the AgentTeams runtime control plane, Matrix rooms, and the RepoMesh
product control plane. Adopted by ADR 0004. New incompatible shapes use a new sibling version
directory; deployed consumers must never infer fields that are absent from their declared
version.

Current version: `v1`.

## Documents

- `external-worker-enrollment.schema.json` — the non-secret binding a Bridge instance needs to
  serve one AgentTeams `containerManaged: false` Worker. Credential fields are opaque
  references; secret values never appear in an enrollment, a room message, a task, or a log.
- `room-observation.schema.json` — the only shape the Bridge may project into a room. Matrix
  text is a display projection of this document; structured truth stays in RepoMesh.

## Interface semantics

These facts are part of the Bridge interface. Implementations and tests must uphold them; they
are not implementation details.

### Validation and startup

- Enrollment validation is fail-fast: an enrollment that does not validate against the schema,
  references a Worker that is not `containerManaged: false`, or names an unknown coding profile
  is rejected before any network connection is opened.
- The Bridge announces readiness only after Matrix sync is established and local state is
  recovered. AgentTeams must not fabricate container readiness for external workers; Bridge
  liveness is reported through its own heartbeat, not inferred from resource existence.

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

- Only events from rooms in the enrollment `allowedRoomIds` are processed; invites outside the
  allowlist are not auto-joined.
- Only messages that mention the enrolled worker's Matrix user id start a turn.
- Conversation sessions run with a deny-all tool permission policy and a throwaway workspace.
  Repository writes require the governed execution path (approved Task, assignee match,
  platform-prepared worktree, Runtime v1 permissions); no room message can widen paths, tools,
  network targets, or credentials.
- Room text never advances Task state. Only persisted Runner events do.

### Lifecycle

- `run` blocks until cancelled. Cancellation terminates the entire CLI process group the Bridge
  started, persists state, and leaves any in-flight governed run to Runtime v1 interruption
  semantics.
