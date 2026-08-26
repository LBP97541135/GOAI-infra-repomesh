# ADR 0004: The Room-Native Coding Agent Bridge Is an Independent Process

- Status: Accepted
- Date: 2026-08-26
- Extends: ADR 0002 (first-party AgentTeams runtime), ADR 0003 (Runner protocol drivers)

## Context

RepoMesh currently splits "the agent in the room" from "the agent that writes code":

- AgentTeams CoPaw/OpenClaw Workers own a Matrix identity and can be mentioned, but they are
  shells that do not run a coding CLI.
- The RepoMesh Runner (ADR 0003) executes claude-code, codex, and kimi headlessly through
  protocol drivers inside an isolated git worktree, but `contracts/runtime/v1/worker-runtime.md`
  deliberately excludes Matrix credentials: the Runner is PID 1 and knows nothing about rooms.
- The Runner engine emits `runner.accepted` plus exactly one terminal event; the schema reserves
  progress/session/test event types but nothing publishes them, so rooms see no execution
  progress.

The AgentTeams controller already supports externally hosted workers: `containerManaged: false`
on a Worker keeps identity, team membership, and room reconciliation while skipping container
create/delete (`member_reconcile.go`, `ReconcileMemberContainer`). The Python projection layer
does not yet carry the field.

An experimental branch (`feat/agentteams-external-cli-runtimes`, 12 commits, 38 files,
+9613/-136) proved the direction end to end with real Matrix and real Claude Code/Codex
sessions, but it re-implemented CLI drivers and asset projection that `repomesh_runner` now owns
in a newer form. Merging it wholesale would create two drifting protocol implementations.

The full analysis, including staged delivery and effort estimates, is in
`docs/development/room-native-coding-agent-bridge-proposal-20260826.md`.

## Decision

We add a first-party **Room-Native Coding Agent Bridge**: a standalone process on the
operator's machine that binds one AgentTeams external Worker identity to one local coding CLI
session, connected to Matrix for conversation and to RepoMesh for governed execution.

1. **Independent process; Runtime v1 unchanged.** The Bridge lives in
   `src/repomesh_agent_bridge/` with its own cross-process contracts under
   `contracts/agent-bridge/v1/`. Runner v1 semantics (PID 1, no Matrix credentials, accepted +
   single terminal event) are not modified. If Runner semantics must ever change, that is a
   Runtime v2 discussion, not a Bridge patch.

2. **External workers via `containerManaged: false`.** RepoMesh provisions Bridge-backed
   workers as explicit external workers. The Go controller needs zero changes; the Python
   `WorkerProjection` gains `container_managed: bool = True`, and only an explicit external
   provisioning path sets it to false.

3. **One Worker : one Bridge instance : one CLI profile** in the first phase. No shared
   in-process state across worker identities. Codex goes first: the app-server driver already
   produces text/tool/permission/session events and supports `thread/resume` with a verified
   native session id.

4. **Interface and seams.** The Bridge is one deep module. Its external interface is:

   ```python
   class RoomNativeAgent:
       async def run(self, enrollment: ExternalWorkerEnrollment) -> None: ...
   ```

   The interface contract (fail-fast enrollment validation, at-most-once turn per Matrix
   event, restart recovery, process-group termination on cancel) is frozen in
   `contracts/agent-bridge/v1/README.md`. Internally, exactly two ports exist at real seams:

   - `RoomPort` — Matrix client adapter in production, in-memory room adapter in tests;
   - `CodingSessionPort` — a Bridge-side adapter over the existing
     `ProtocolDriver.execute(request, profile, observer)` in production, a scripted adapter in
     tests. The Runner driver stack is consumed, not copied and not modified.

   Deliberately **not** ports in phase 1: local state (SQLite is its own test stand-in; the
   seam stays internal), credential resolution (an injected `resolve(ref) -> secret` callable),
   and the RepoMesh task/event surface (no second adapter exists before governed execution
   lands; introducing those ports earlier would be indirection without variation).

5. **Conversation and execution are separate session tracks.** Room conversation sessions are
   keyed by Worker + Room, run with a deny-all permission policy and a throwaway workspace
   directory, and never inherit a writable worktree. Governed execution is keyed by
   Worker + Task + Run and flows through the existing `start_assigned_task` gate, worktree
   preparation, context materialization, and Runner event sink. Room text never advances Task
   state; only persisted Runner events do.

6. **Idempotency has three stable keys**: Matrix `room id + event id` for inbound dedup,
   `worker + native session + trigger event` for CLI turns, and the Runtime v1
   `idempotencyKey + event sequence` for governed execution. Outbound room messages use the
   deterministic Matrix transaction-id scheme already implemented in
   `repomesh.integrations.agentteams.matrix`.

7. **Room visibility is allowlist-only.** Rooms receive versioned
   `repomesh.room-observation.v1` payloads (lifecycle, phase, sanitized tool actions, changed
   files, test results, questions, evidence references). Reasoning/`THINKING` events, system
   prompts, credentials, raw protocol frames, and unsanitized stdout/stderr are never
   projected.

8. **The experimental branch is a quarry, not a merge source.** We extract its reliability
   designs (sync cursor baseline, trusted-invite handling, bounded seen-set, turn ledger,
   session store, supervisor shutdown) and its test scenarios as behaviour contracts. Its CLI
   drivers and projectors are superseded by `repomesh_runner` and are not extracted.

9. **Execution-plane repair is a prerequisite track, not part of the Bridge.** The known
   materialize failures (missing handoff_docs migration, default-path mismatch on established
   repositories), the missing runner image, and the absent compose Runner consumer must be
   fixed before the governed-execution stage; they proceed in parallel and do not block the
   conversation-only milestones.

## Consequences

- The first shippable milestone is a room member with zero repository write access:
  mention → reply → dedup → restart recovery, verified only through the `RoomNativeAgent`
  interface with an in-memory room adapter and a scripted coding session.
- Bridge tests and Runner driver tests share behaviour contracts at the `CodingSessionPort`
  seam, so driver drift breaks both suites instead of silently forking.
- Because the Bridge consumes the Runner task source directly for its worker, we avoid running
  a second local Runner consumer competing for the same worker's tasks.
- If phase work shows the Bridge needs capabilities `ProtocolDriver` does not expose (for
  example mid-turn cancellation), the escalation path is extracting a shared coding-session
  interface inside `repomesh_runner` — a deliberate follow-up decision, not an implicit edit.
