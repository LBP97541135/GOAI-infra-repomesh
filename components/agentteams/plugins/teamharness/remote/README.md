# TeamHarness Remote Member — Claude Code and Codex CLI

Local-first bridge that lets a Claude Code install on an operator's own machine
join an AgentTeams team as a full member, using the operator's existing Claude
subscription. No model API key, no worker container, no credential upload.

Status: **working end to end against a live deployment.** 139 unit tests
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
# from plugins/teamharness/remote/, with
# AGENTTEAMS_MATRIX_URL / AGENTTEAMS_WORKER_MATRIX_TOKEN in the environment
```

Add `--runtime codex-cli` to drive Codex instead of Claude Code. The runtime
selects which driver and projector are used and nothing else; supervision,
dedup, and session state are identical for both.

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
remote/
├── README.md
└── bridge/                 # shared by every runtime; imported, never forked
    ├── protocol.py         # AssetProjector + RuntimeDriver contracts
    ├── session_store.py    # task_id -> resume handle, crash-durable
    ├── dedup.py            # cursor + seen-set + turn ledger + ack watermark
    ├── bootstrap.py        # operator-written local config (contract-subset YAML)
    ├── supervisor.py       # main loop: poll -> claim -> turn -> forward -> ack
    ├── runtimes.py         # name -> (driver, projector); one entry per runtime
    ├── drivers/
    │   ├── _process.py     # spawn/reap/redact plumbing, protocol-agnostic
    │   ├── claude_code.py  # `claude -p --output-format stream-json`
    │   └── codex_cli.py    # `codex exec --json`
    └── projectors/
        ├── _assets.py      # marker algebra + manifest role filter
        ├── claude_code.py  # CLAUDE.md + .claude/skills + .mcp.json
        └── codex_cli.py    # AGENTS.md + .codex/skills (no MCP file; see below)
```

`bridge/` began life under `remote/claude-code/`. Adding Codex forced the
promotion its own `__init__.py` had already committed to -- a directory named
after one runtime is not a home for shared code.

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

## Codex CLI

Driven through `codex exec --json`, not the `codex app-server` JSON-RPC this
bridge's design note originally predicted: `app-server` is marked
`[experimental]`, while `exec` is the supported non-interactive entry point and
has a first-class `resume` subcommand. The event stream was captured from a
real `codex-cli 0.145.0` run; the traps it encodes are listed in
`bridge/drivers/codex_cli.py` and each one cost a probe to find.

Two of the three asset targets map cleanly -- `AGENTS.md` for `CLAUDE.md`,
`.codex/skills` for `.claude/skills`. **MCP config does not.** Codex has no
project-level MCP configuration; servers live in the global
`~/.codex/config.toml`, alongside everything else the operator runs. Projecting
there would make joining a team mutate machine-wide configuration and leaving
one mean editing it back, so the Codex projector writes **no MCP config at
all** and the driver declares the server with per-invocation `-c` overrides
instead. Nothing is installed, so nothing needs uninstalling.

That choice moves the declaration from a file into the argument list, which is
world-readable on a shared machine -- worse than a file, not better. So it
carries only the non-secret role and encoding pins: no values, and not even
`${VAR}` references. Credentials reach the MCP server the same way they reach
Codex itself, by environment inheritance from the bridge process, which never
serialises them anywhere.

**The `-c` approach is confirmed against a live team.** Codex saw all seven
TeamHarness tools, and `message` was *absent* from the list -- which is the
proof that the `env` block reached the server, since hiding `message` is
role-based filtering keyed on `AGENTTEAMS_AGENT_ROLE`. The environment does
reach the MCP child.

**An MCP tool call needs a second grant**, exactly as it does for Claude Code.
Without one it returns
`{"isError": true, "content": [{"text": "user cancelled MCP tool call"}]}` --
projecting config makes tools *visible*, not *callable*, and the failure reads
like a broken MCP server. The mechanisms differ: Claude Code takes
`--allowedTools mcp__teamharness__<tool>`; Codex takes

```yaml
    - -c
    - mcp_servers.teamharness.default_tools_approval_mode="approve"
```

Found by reading the config struct out of the binary. Three near-misses worth
recording, because each looks plausible: `enabled=true` does nothing,
`projects.<path>.trust_level="trusted"` gates the *project* and not MCP, and of
the four `default_tools_approval_mode` values only `approve` works (`auto`,
`prompt` and `writes` all still deny). `--dangerously-bypass-approvals-and-
sandbox` also works but throws away the sandbox to buy one grant; `approve`
keeps it.

This stays out of the defaults for the same reason `driverArgs` is empty: how
much authority a remote agent gets on someone else's laptop is the operator's
call.

**The MCP server also needs to be told where the workspace is.** A worker
container's server infers it from `QWENPAW_WORKING_DIR`; a remote member has no
equivalent, so `ack_task` came back
`{"ok": false, "error": "workspaceDir is required"}`. Both runtimes now project
`TEAMHARNESS_SHARED_DIR=<workspace>/shared` into the MCP environment -- a path,
not a credential. This was invisible until a live turn actually called the tool.

Verified against a live embedded deployment (team `e2e-remote`, member
`bohan-codex`, `containerManaged: false`, no container):

- the bridge auto-accepted the team-room invite from `@admin` and joined;
- the first poll took a baseline and executed nothing;
- an `@mention` drove a real Codex turn that wrote the requested file with
  exactly the requested contents, and the answer came back as a threaded room
  reply;
- a follow-up in the same thread resumed the same Codex thread id and answered
  from the first turn's context (`turn_count: 2`, one `session_ref`).

- with the grant above, the full task protocol closed: `taskflow ack_task`
  returned `ok`, the deliverable landed in
  `shared/tasks/<id>/workspace/`, `submit_task` moved the task to
  `submitted`, and the member reported `TASK_COMPLETED` to the leader in the
  team-protocol format.

With `mc` installed, shared storage closed too, on **both** runtimes: a
delegated task's deliverable was pushed with `filesync`, confirmed with
`filesync stat`, and then read back out of MinIO by an independent admin client
— `MANIFEST-V1` for `bohan-codex` (Codex CLI) and `CLAUDE-RELEASE-1` for
`bohan-local` (Claude Code). The scoped policy held: the member can list
`shared/`, and both another member's prefix and the bucket root answer
`Access Denied`.

That run also settled the one thing this README had listed as unverified —
whether a runtime passes its environment to a stdio MCP child. Claude Code
expands the `${VAR}` references in `.mcp.json`. Codex passes **nothing**, and
its `env` table is the child's entire environment rather than an overlay on it;
`mcp_servers.<id>.env_vars` is the companion field that inherits by *name*, so
the passthrough set still crosses without a single value being serialised.

Getting that wrong was silent, not loud, which is why it survived a full green
test suite: with no `AGENTTEAMS_STORAGE_PREFIX` the server built an unprefixed
remote path, `mc` read it as a *local* directory, and `filesync push` answered
`{"ok": true}` for a file that never left the disk. `filesync` now refuses an
unconfigured prefix instead of quietly copying sideways.

## Shared storage

An earlier draft of this README declared shared storage unavailable to a remote
member and said granting it "needs a decision about credentials". That was
wrong, and the mistake is worth recording because it is the same mistake the
whole bridge exists to avoid: assuming a capability is missing because the
container is.

**The member already has its own scoped storage credentials.** In embedded mode
the provisioner creates a per-member MinIO user and attaches a policy to it
([`service/provisioner.go`](../../../../agentteams-controller/internal/service/provisioner.go)):

```go
p.ossAdmin.EnsureUser(ctx, workerName, creds.MinIOPassword)
p.ossAdmin.EnsurePolicy(ctx, oss.PolicyRequest{WorkerName: workerName, TeamName: req.TeamName})
```

That runs during identity provisioning — the same phase that mints the Matrix
user and the rooms — which is *before* `ReconcileMemberContainer` reaches the
`containerManaged` skip. A remote member has a MinIO account for the same
reason it has a Matrix account. The policy scope
([`accessresolver/defaults.go`](../../../../agentteams-controller/internal/accessresolver/defaults.go))
is the member's own `agents/<name>/` prefix plus the shared and team prefixes;
the leader is not elevated above it.

The MinIO username is the member name and the password is `WORKER_MINIO_PASSWORD`
in the worker credentials Secret — the same Secret the operator already reads
`AGENTTEAMS_WORKER_MATRIX_TOKEN` from.

**No new code is needed to consume it.** `_filesync_mc_env` in `mcp/server.py`
already builds `MC_HOST_agentteams` from the environment when the alias is not
preconfigured, so the operator sets four variables and `filesync` configures
itself:

```bash
AGENTTEAMS_FS_ENDPOINT      # MinIO S3 endpoint reachable from the host
AGENTTEAMS_FS_ACCESS_KEY    # = the member name
AGENTTEAMS_FS_SECRET_KEY    # = WORKER_MINIO_PASSWORD from the worker Secret
AGENTTEAMS_STORAGE_PREFIX   # e.g. agentteams/agentteams-storage
```

All four are in `DEFAULT_MCP_ENV_PASSTHROUGH`, so the projected `.mcp.json`
carries `${VAR}` references for them with no bootstrap change. Names only — the
credential red line is unchanged, and unset variables degrade to exactly the
error `filesync` returns today.

This reuses the existing `filesync` tool and its existing environment entry
point. It writes no file-sync layer, which is the specific thing PR #828 was
closed for duplicating.

**Two real gaps remain, neither architectural:**

- `mc` is not on an operator's laptop. Short term the operator installs it;
  the missing-binary error already says so in plain language instead of
  surfacing a bare `WinError 2` that reads as a path bug. Longer term,
  replacing the `mc` subprocess with a pure-Python MinIO client removes the
  dependency — that has independent value for every Windows user and should go
  upstream on its own.
- Endpoint reachability from the host is a **real** gap, now measured rather
  than guessed: in embedded mode MinIO listens on `127.0.0.1:9000` *inside* the
  controller and reaches other containers only through the service mesh. Port
  9000 is not published, and unlike Matrix there is no gateway route either, so
  a remote member has no `AGENTTEAMS_FS_ENDPOINT` it can dial. The E2E above ran
  through a loopback-only forwarder standing in for one. Closing this properly
  is a deployment change, not a bridge change. Matrix HTTP
  already works from the host, which is weak evidence the ports are published.

**Cloud / in-cluster mode is out of scope, by declaration.** There `ossAdmin`
is nil and credentials come from `POST /api/v1/credentials/sts`, authenticated
by Kubernetes TokenReview — a laptop has no ServiceAccount token. Supporting it
would mean adding a remote-member-capable authentication path to that existing
endpoint, still not a new layer. Until then: embedded mode gets full shared
storage, cluster mode gets none.

One related fix went in while diagnosing the original symptom: `call_tool`
converts *any* unhandled tool exception into a tool-level error. Previously
`FileNotFoundError` propagated to the stdio loop and killed the server, so one
missing optional binary cost the client every tool, with no way to tell "this
call failed" from "the server is gone".

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

**3. (Decided) Security parity is not achievable, and is now declared rather
than faked.**
The qwenpaw adapter is an in-process plugin: it can wrap every tool result
(`sanitize_tool_result`) and mutate the runtime file guard live
(`apply_credential_guard`). Claude Code can only approximate this with
`PreToolUse` / `PostToolUse` hooks and `permissions.deny`; Codex has no
equivalent. Resolution: the "Credential Eligibility" section of
[`docs/teamharness-boundary-and-contracts.md`](../../../../docs/teamharness-boundary-and-contracts.md)
declares a `containerManaged: false` member ineligible for
`spec.credentialBindings`, for `config/credagent.json` protected credentials,
and for `spec.accessEntries` scopes beyond its own room and workspace prefix.

## Choosing the runtime, and starting only when it works

Which CLI drives a member is a laptop-side fact. Nothing in the cluster knows or
needs to: a `containerManaged: false` Worker has no container image to name, and
`spec.runtime` on the Worker CR describes what runs *inside* a container. The
controller provisions identity, rooms and storage; the operator picks the CLI.

State it in the bootstrap file, and override it per run if you want:

```yaml
local:
  runtime: codex-cli       # or claude-code; --runtime beats this
  workspace: /path/to/repo
```

Careful: this is **not** `member.runtimeName`, which is the AgentTeams agent
name behind the `agents/{runtimeName}/` storage prefix. Writing a CLI name there
moves the storage prefix and leaves the runtime on its default, silently.

**The bridge will not start against a CLI it cannot use.** It probes — binary on
PATH, `--version`, credential file present — and if the runtime is not ready it
waits, re-probing every few seconds, printing what to do:

```
WARNING waiting for codex-cli: no local credentials found; run `codex login` once
INFO    codex-cli is ready: /usr/local/bin/codex (codex-cli 0.145.0)
```

So the ordinary sequence is: start the bridge, see the hint, sign in *in another
terminal*, and it picks up on its own. No restart. `--wait-for-runtime` sets the
budget (default 300s); `0` exits immediately, which is what a supervised or
scripted start wants since its restart policy is the retry loop.

Refusing to start is a correctness fix, not a nicety. The poll loop acks a batch
once it has walked it — `handled_ids` is *every* event in the batch, not the
ones that succeeded. A bridge that joined the room without a working CLI would
take a first-run baseline over the backlog and then ack each new task as its
turn failed; signing in afterwards recovers none of it, because those events are
past the cursor and in the seen-set. The tasks are gone and the room never says
so.

The gate cannot be airtight, because `authenticated` is a file-existence check —
opening a credential file is forbidden — and for Codex a bare `config.toml`
satisfies it. So a signed-out laptop can still pass. That case, and a session
whose token expires mid-run, are caught on the other side: when a turn fails,
the bridge re-probes, and if the runtime has gone it stops **before** the ack,
settles the turn non-terminally, and forwards nothing. The batch replays on the
next start. Forwarding a failure would be the one irreversible act — the room
would carry a "could not do it" that the replay then contradicts.

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

```bash
ruby plugins/scripts/validate-plugin.rb plugins/teamharness/plugin.yaml
ruby plugins/tests/teamharness/test-contracts.rb
ruby plugins/tests/teamharness/mcp/test-server.rb
```

All three pass. They were unverified for a while because ruby was missing from
the dev environment, and both failures that surfaced on first run were in the
gates rather than in the code:

- `test-contracts.rb` still asserted the *old* `remote-member` prompt wording
  (`"current room/session"`, `"must leave"`), which open question 2 above had
  deliberately rewritten. The assertions now check what the resolved contract
  actually says: `message` is unavailable, `taskflow` / `artifact` are the
  channels that leave the conversation, and the agent does not post its own
  progress.
- `mcp/test-server.rb` invoked `python3`, which on Windows resolves to the App
  Execution Alias stub — it exits non-zero and prints nothing, so the harness
  reported a server failure with an empty explanation. It now tries `python3`
  then `python`, and reports what each attempt actually did.
