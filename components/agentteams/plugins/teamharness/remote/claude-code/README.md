# TeamHarness Remote Member — Claude Code

Local-first bridge that lets a Claude Code install on an operator's own machine
join an AgentTeams team as a full member, using the operator's existing Claude
subscription. No model API key, no worker container, no credential upload.

Status: **working end to end against a live deployment.** 77 unit tests
(`plugins/tests/teamharness/remote/`, no network / no mock.patch) plus a manual
E2E against an embedded-mode AgentTeams: a `containerManaged: false` Worker CR
was provisioned with no container, its Matrix identity was used by this bridge
from the host, a `@mention` from the manager drove a real Claude Code turn that
wrote a file in the workspace, and the answer came back as a threaded room
reply. A follow-up in the same thread resumed the same runtime session and
answered from its earlier context.

Confirmed in the controller's own log during that run:

```
"msg":"container management disabled for member, skipping"  worker=bohan-local
"msg":"worker created"
```

Run it:

```bash
python -m bridge.supervisor --bootstrap ~/.agentteams/remote/bootstrap.yaml
# from plugins/teamharness/remote/claude-code/, with
# AGENTTEAMS_MATRIX_URL / AGENTTEAMS_WORKER_MATRIX_TOKEN in the environment
```

## Why this shape

The controller already supports everything needed on the cluster side:

- `Worker.spec.containerManaged: false` skips pod reconciliation
  ([`member_reconcile.go`](../../../../agentteams-controller/internal/controller/member_reconcile.go))
  while still provisioning Matrix identity, rooms, and storage prefixes.
- `spec.runtime` only selects a container image
  ([`backend/interface.go`](../../../../agentteams-controller/internal/backend/interface.go)),
  so it is inert once container management is off.

**No controller change and no CRD enum change is required.** That is the whole
point: PR #569 and PR #828 were closed because they added a standalone runtime
that duplicated Matrix, file sync, policy, and session layers. Those layers now
live behind the TeamHarness MCP tools, and this bridge consumes them instead of
reimplementing them.

## Boundaries

| Concern | Owner |
|---|---|
| Matrix identity, rooms, storage prefixes | Controller (unchanged) |
| Outbound task lifecycle, shared files, room artifacts | TeamHarness MCP (`taskflow` / `filesync` / `artifact`) |
| Inbound room events | TeamHarness MCP (`inbox`, added by this work) |
| Cursor durability, dedup, turn idempotency | `bridge/dedup.py` |
| Task ↔ runtime session mapping | `bridge/session_store.py` |
| Process supervision, deadlines, cancel | `bridge/supervisor.py` |
| Prompt/skill/MCP projection into Claude Code | `bridge/projectors/claude_code.py` |

Per [`docs/teamharness-boundary-and-contracts.md`](../../../../docs/teamharness-boundary-and-contracts.md),
the bridge reads controller-written runtime facts and does **not** shell out to
`agt` for team or member identity. See open question 1 below — that contract is
not yet implemented on master.

## Layout

```text
remote/claude-code/
├── README.md
└── bridge/
    ├── __init__.py
    ├── protocol.py        # AssetProjector + RuntimeDriver contracts
    ├── session_store.py   # task_id -> resume handle, crash-durable
    ├── dedup.py           # cursor + seen-set + turn ledger + ack watermark
    ├── bootstrap.py       # operator-written local config (contract-subset YAML)
    ├── supervisor.py      # main loop: poll -> claim -> turn -> forward -> ack
    ├── drivers/
    │   └── claude_code.py # RuntimeDriver over `claude -p --output-format stream-json`
    └── projectors/
        └── claude_code.py # AssetProjector: CLAUDE.md + .claude/skills + .mcp.json
```

`bridge/` is deliberately runtime-neutral. When `remote/codex-cli/` lands it
should import this package, not fork it — if the Codex driver needs changes
here, the abstraction was drawn wrong.

## Two protocols, not one

`protocol.py` splits execution from asset projection:

- **`AssetProjector`** — writes `CLAUDE.md`, `.claude/skills/`, `.mcp.json`.
  Ships in `bridge/projectors/claude_code.py`.
- **`RuntimeDriver`** — drives one bounded turn over the headless protocol.
  Ships in `bridge/drivers/claude_code.py`.

Fusing them would force the supervision logic to be rewritten per runtime,
which is what made PR #828 a 49-file branch.

`RuntimeDriver.run_turn` is a **generator**, so the supervisor owns the clock:
it stops consuming at the deadline and calls `cancel`, and the driver needs no
timeout logic. Both Claude Code stream-json and Codex `app-server` JSON-RPC map
onto this.

Drivers must emit a `session_ref` event **as soon as the runtime reveals the
handle**, not at turn end. PR #828 shipped the opposite bug: `matrix_relay.py`
always passed `None` as `session_id`, so `--resume` never fired.

## Delivery semantics

`inbox` gives at-least-once delivery. `dedup.py` turns that into at-most-once
execution through three separate pieces of state:

1. **Cursor** advances only on `ack`, never on read. A cursor that advanced on
   read would turn a crash into silently dropped work.
2. **Seen-set** (bounded, persisted) makes the resulting replay harmless.
3. **Turn ledger** keyed by `(task_id, trigger_event_id)` guards the expensive
   side effect. A turn still marked `in_flight` on startup is *regranted* —
   that means the previous bridge died mid-turn and retrying is correct. A turn
   that reached a terminal state is refused.

`timeout` is deliberately **not** terminal, so a longer deadline or a manual
nudge can pick the task back up.

Two rules the supervisor must obey, both enforced or surfaced by `dedup.py`:

- **First-run baseline** (`InboxState.first_run`): with no cursor, the first
  sync returns recent *history*, including mentions that predate this member
  or were already handled elsewhere. The first poll is ack-only — establish
  the baseline, execute nothing. Skipping this replays old task assignments
  against a live workspace.
- **Gap backfill**: when `inbox` reports `gaps`, the room's timeline
  overflowed one sync batch and the server dropped the middle. The supervisor
  pages through `backfill` until it meets an already-seen event id before
  acking the batch. Ignoring `gaps` means a burst can swallow a task
  assignment.

`claim_turn` also refuses a trigger this process is *currently* executing
(`reason="active"`, via a non-persisted in-process set) — the persisted
`in_flight` retry semantics only apply across process deaths, not across
threads of a live bridge.

Sessions are keyed by task, never by room. One room carries several concurrent
tasks; keying by room is how one task's context bleeds into another
(upstream issue #603).

## The `inbox` MCP tool

Added to the TeamHarness base package rather than kept private here, because
every future CLI runtime needs the same thing and TeamHarness already owns the
Matrix credentials and HTTP path.

It is **stateless**: the caller passes `since` and gets `nextBatch` back.
Cursor durability belongs to whoever owns crash semantics. `ack` echoes the
caller's cursor so the agent-side and bridge-side call shapes stay identical,
leaving room for a server-side cursor later without a contract change.

Completeness: `poll` reports server-truncated rooms in `gaps`
(`{roomId, prevBatch}`), and `backfill` pages backwards through a gap via
`/rooms/{id}/messages`. There is deliberately no post-hoc result slicing —
slicing while advancing `nextBatch` would drop events unrecoverably.

Events are normalized (`eventId` / `roomId` / `sender` / `ts` / `kind` /
`body` / `mentionsMe` / `threadRootId`). A driver should never have to know
what `m.mentions` is.

Base-package files touched:

- `mcp/inbox_tool.py` (new)
- `mcp/server.py` — import, `TOOL_NAMES`, `TOOL_SCHEMAS`, `call_tool`, `_inbox`
- `plugin.yaml` — `mcp.servers[0].tools`
- `plugins/tests/teamharness/test-contracts.rb` — expected tool list

## Shared storage is not available to a remote member

`taskflow` and `filesync` shell out to the MinIO client (`mc`), which ships in
the worker image but is not on an operator's laptop, and the member has no
MinIO credentials either. `ack_task` and `submit_task` still work — task state
under `<workspace>/shared/tasks/` is local — but the pull/push half degrades to
an error result rather than syncing. Two fixes went in when this surfaced:

- `filesync` reports a missing `mc` in plain language instead of surfacing a
  bare `WinError 2`, which reads as a path bug and sends people looking in the
  wrong place.
- `call_tool` converts *any* unhandled tool exception into a tool-level error.
  Previously `FileNotFoundError` propagated to the stdio loop and killed the
  server, so one missing optional binary cost the client every tool, with no
  way to tell "this call failed" from "the server is gone".

Giving a remote member real shared storage needs a decision about credentials
that this draft does not make.

## Open questions

**1. Who writes `runtime.yaml` for a remote member?**
[`docs/member-runtime-config-contract.md`](../../../../docs/member-runtime-config-contract.md)
defines `shared/runtime/members/{memberName}/runtime.yaml` as the source of
team/member facts, and the qwenpaw adapter already reads it. But nearly every
field in that document is annotated `# master current: ... not injected to
worker` — the controller does not write the file today. A remote member has no
in-cluster process to fall back on, and the boundary doc forbids querying `agt`.
Until this is resolved the bridge needs an explicit local bootstrap file.

**2. (Decided) `message` stays blocked for `remote-member`; the prompt was
wrong.**
`server.py` blocks the tool via `MESSAGE_TOOL_BLOCKED_ROLES` and hides it from
`tools/list`; the role prompt used to instruct the opposite.
Resolution: code is authoritative. **All room output goes through the bridge
(final-answer forwarding) or through `artifact`/`taskflow`.**
[`prompts/agent/remote-member.md`](../../prompts/agent/remote-member.md) has
been rewritten to match, which adds one bridge obligation: forward the turn's
final text to the triggering room.

**3. Security parity is not achievable and should be stated, not faked.**
The qwenpaw adapter is an in-process plugin: it can wrap every tool result
(`sanitize_tool_result`) and mutate the runtime file guard live
(`apply_credential_guard`). Claude Code can approximate this with
`PreToolUse` / `PostToolUse` hooks and `permissions.deny`; Codex has no
equivalent. Rather than pretend parity, remote members should be declared
ineligible for sensitive credential bindings in the contract.

## Granting the agent authority

`local.driverArgs` in the bootstrap file is appended verbatim to every runtime
invocation and is how the operator decides what the agent may do:

```yaml
local:
  workspace: /path/to/repo
  driverArgs:
    - --permission-mode
    - acceptEdits
    - --allowedTools
    - mcp__teamharness__taskflow,mcp__teamharness__artifact,mcp__teamharness__filesync,mcp__teamharness__health,mcp__teamharness__projectflow
```

It defaults to empty on purpose. Headless Claude Code declines edits without
it, so a bridge started with no `driverArgs` answers every coding task with "I
need permission to write" — that was the first E2E result, and wiring a default
here would be this code deciding how much authority a remote agent gets on
someone else's laptop.

**Two separate grants, and the second is easy to miss.** `--permission-mode`
covers file edits; MCP tools need `--allowedTools` naming them explicitly as
`mcp__teamharness__<tool>`. Projecting `.mcp.json` makes the tools *visible*,
not *callable*: without the second grant the agent finds `taskflow`, tries it,
and reports back that permission was never granted — it looks like a broken
MCP server and is not one. `message` is deliberately absent from the list
above; it is blocked for `remote-member` server-side anyway.

## Credential rules

Non-negotiable, and worth asserting in CI:

- `~/.claude/.credentials.json` is never read, copied, packaged, logged, or
  uploaded. The bridge does not authenticate Claude Code; the operator does.
- Matrix token, gateway key, and storage keys stay in environment variables
  (`AGENTTEAMS_MATRIX_URL`, `AGENTTEAMS_WORKER_MATRIX_TOKEN`, …) and never land
  in any snapshot file the bridge writes. `AssetContext.mcp_env_passthrough`
  carries variable **names** only — projected `.mcp.json` uses `${VAR}`
  references, expanded by the runtime at spawn time.
- `bridge/` state files (`sessions.json`, `inbox.json`) hold ids and cursors
  only.

## Validation

```bash
python -m compileall -q plugins/teamharness/mcp plugins/teamharness/remote
```

`ruby plugins/scripts/validate-plugin.rb` and
`ruby plugins/tests/teamharness/test-contracts.rb` also gate this change, but
ruby is not installed in the current dev environment — the tool-list assertion
in `test-contracts.rb:262` has been updated by hand and is unverified.
