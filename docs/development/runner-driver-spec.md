# RepoMesh Runner Driver Layer — Design Spec v0.1

- Date: 2026-08-03
- Decision record: `docs/adr/0003-runner-protocol-drivers.md`
- Owner: runtime-integrations (`src/repomesh_runner`)
- Status of this document: interfaces in section 3 are frozen for the current milestone;
  wire details in sections 5–6 may be corrected against real CLIs without an ADR change.

## 1. Goal and scope

Give `RunnerExecutor` (the empty port in `src/repomesh_runner/engine.py`) its first real
implementation: spawn a headless coding CLI, speak its protocol, enforce permissions, and
return a fail-closed `RunnerExecutionResult`.

In scope (this milestone):

- Driver contract, process supervision core, profile registry.
- `stream-json` driver + `claude-code` profile.
- `acp` driver + `kimi` profile.
- `app-server` driver + `codex` profile.
- `DriverExecutor` implementing the existing `RunnerExecutor` protocol.
- Contract tests against scripted fake processes; env-gated real-machine smoke tests.

Out of scope (later milestones):

- `one-shot` driver family.
- Worktree creation, context bundle mounting, test harness, artifact upload.
- Event transport to RepoMesh and the AgentTeams worker image.
- Reconciling `integrations/coding_agents` catalog against profiles; restore/resume flows.
- Streaming intermediate `runner.progress` events through `ExecuteRunnerTask` (the observer
  exists, but engine wiring stays two-event: accepted + terminal).

## 2. Module structure

```text
src/repomesh_runner/
├─ contracts.py              # existing Runtime v1 types — DO NOT MODIFY
├─ engine.py                 # existing ExecuteRunnerTask — DO NOT MODIFY
├─ executor.py               # NEW  DriverExecutor: RunnerTask -> profile -> driver -> result
├─ profiles.py               # NEW  declarative CLI profile registry (claude-code, kimi)
└─ drivers/
   ├─ __init__.py            # re-exports of the public contract
   ├─ base.py                # NEW  frozen driver contract (section 3)
   ├─ supervision.py         # NEW  process factory, termination, idle watchdog, stderr tail
   ├─ stream_json.py         # NEW  StreamJsonDriver
   ├─ acp.py                 # NEW  AcpDriver
   └─ app_server.py          # NEW  AppServerDriver (codex)

tests/runner/
├─ test_engine.py            # existing — must stay green
├─ conftest.py               # scripted FakeProcess / FakeProcessFactory
├─ test_driver_supervision.py
├─ test_stream_json_driver.py
├─ test_acp_driver.py
├─ test_app_server_driver.py
├─ test_executor.py
└─ test_smoke_real_clis.py   # skipped unless the CLI binary is installed AND
                             # REPOMESH_RUNNER_SMOKE=1
```

Dependency rules: `drivers/` and `profiles.py` import only the standard library and
`repomesh_runner.drivers.base`. Nothing under `src/repomesh_runner/` imports `repomesh.*`
(the Runner ships into Worker containers without the control plane). `executor.py` imports
`contracts.py` types only.

## 3. Frozen contract (`drivers/base.py`)

```python
class DriverFamily(StrEnum):
    STREAM_JSON = "stream_json"
    ACP = "acp"
    APP_SERVER = "app_server"      # reserved, no driver this milestone
    ONE_SHOT = "one_shot"          # reserved

class DriverResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"    # cancelled / signalled
    INPUT_REQUIRED = "input_required"
    TIMEOUT = "timeout"            # idle watchdog fired

class PermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"          # cannot decide locally -> run ends INPUT_REQUIRED

class PermissionPolicy(Protocol):
    def decide(self, tool_name: str, tool_input: Mapping[str, object]) -> PermissionDecision: ...

@dataclass(frozen=True, slots=True)
class DriverRequest:
    executable: str                       # resolved binary path
    workspace: Path
    prompt: str
    environment: Mapping[str, str]        # extra env, merged over os.environ by supervision
    permission_policy: PermissionPolicy
    model: str | None = None
    system_prompt: str | None = None
    resume_session_id: str | None = None
    extra_arguments: tuple[str, ...] = ()  # pre-resolved argv extras (e.g. permission mode),
                                           # computed by the executor; drivers append verbatim
    idle_window_seconds: float = 180.0    # no protocol activity, no tool in flight
    tool_window_seconds: float = 900.0    # no protocol activity while a tool call is in flight

class DriverEventKind(StrEnum):
    SESSION_STARTED = "session_started"   # payload: native_session_id, transcript_path?
    TEXT = "text"
    THINKING = "thinking"
    TOOL_USE = "tool_use"                 # payload: call_id, tool_name, input
    TOOL_RESULT = "tool_result"           # payload: call_id, output?
    PERMISSION_REQUEST = "permission_request"  # payload: tool_name, decision
    LOG = "log"

@dataclass(frozen=True, slots=True)
class DriverEvent:
    kind: DriverEventKind
    payload: Mapping[str, object]

DriverObserver = Callable[[DriverEvent], None]   # sync, must never raise

@dataclass(frozen=True, slots=True)
class DriverResult:
    status: DriverResultStatus
    summary: str                          # deliverable text; MUST be "" unless SUCCEEDED
    native_session_id: str | None = None
    transcript_path: str | None = None
    diagnostics: str = ""                 # protocol errors + bounded stderr tail
    tool_call_count: int = 0

class ProtocolDriver(Protocol):
    family: DriverFamily
    async def execute(
        self,
        request: DriverRequest,
        profile: "CliProfile",
        observer: DriverObserver,
    ) -> DriverResult: ...
```

Session-id hygiene (in `base.py`): `sanitize_session_id(value) -> str | None` returns None
for ids that are empty, longer than 512 chars, start with `-`, or contain control characters.
Sanitization failures downgrade to "no session captured", never crash a run.

Fail-closed rules (enforced by every driver, tested per driver):

1. `SUCCEEDED` requires the protocol's terminal success event. Clean exit without it is
   `FAILED` with diagnostics `"stream ended without terminal result"`.
2. Any non-`SUCCEEDED` result has `summary == ""`; partial text goes to diagnostics-adjacent
   channels (observer TEXT events), never into the deliverable.
3. Precedence when multiple causes overlap:
   cancel > idle timeout > protocol error > process exit error > missing terminal event.

## 4. Profiles (`profiles.py`)

```python
@dataclass(frozen=True, slots=True)
class CliProfile:
    id: str                               # matches RunnerTask.adapter_id
    family: DriverFamily
    binaries: tuple[str, ...]
    launchable: bool
    observable: bool
    resumable: bool
    base_arguments: tuple[str, ...]       # after the binary, before dynamic args
    model_flag: str | None = None
    system_prompt_flag: str | None = None
    resume_flag: str | None = None        # None => resume unsupported by this surface
    permission_arguments: Mapping[RunnerPermissionMode-like str, tuple[str, ...]] = {}
    stream_json: StreamJsonConfig | None = None
    acp: AcpConfig | None = None

@dataclass(frozen=True, slots=True)
class StreamJsonConfig:
    prompt_via_stdin: bool = True

@dataclass(frozen=True, slots=True)
class AcpConfig:
    protocol_version: int = 1
    quiescence_seconds: float = 2.0
```

Registry entries this milestone (binary resolution reuses a minimal local `which`-style
helper in `supervision.py`; no import from `repomesh`):

- `claude-code`: family `STREAM_JSON`, binaries `("claude",)`, base args
  `("-p", "--output-format", "stream-json", "--input-format", "stream-json", "--verbose")`,
  model `--model`, system prompt `--append-system-prompt`, resume `--resume`,
  permission args: `accept_edits -> ("--permission-mode", "acceptEdits")`,
  `default -> ("--permission-mode", "default")`. Never `--session-id` (ids are captured, not
  assigned; reuse of an id is a hard CLI error). Never `bypassPermissions` args.
- `kimi`: family `ACP`, binaries `("kimi",)`, base args `("acp",)`, model handled via
  `session/set_model` when a model is requested (hard-fail the run if the RPC errors),
  resume via `session/resume` (config flag, not argv).

## 5. stream-json driver (`stream_json.py`)

Spawn: `executable + base_arguments + permission/model/system-prompt/resume args`. The prompt
is NOT in argv. After spawn, a dedicated task writes one NDJSON frame to stdin and leaves
stdin open (protocol back-frames arrive on it):

```json
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"<prompt>"}]}}
```

Read stdout line-by-line (bounded line length 10 MB). Handle event `type`:

- `system`: capture `session_id` (first pin), optional transcript path.
- `assistant`: accumulate text blocks; a turn containing `tool_use` marks its text as
  narration (not deliverable). Emit TEXT / THINKING / TOOL_USE events.
- `user`: tool results; emit TOOL_RESULT.
- `control_request` (subtype `can_use_tool`): consult `PermissionPolicy`.
  ALLOW -> write `control_response` `{behavior: "allow", updatedInput: <input>}`;
  DENY -> `{behavior: "deny", message: "denied by RepoMesh policy"}`;
  ESCALATE -> terminate run as INPUT_REQUIRED.
- `result`: terminal. `is_error: false` -> SUCCEEDED with the result text as summary;
  `is_error: true` -> FAILED. Overwrite pinned session id if present.

The exact `control_request`/`control_response` frame shapes must be verified against a live
authenticated claude before the profile is marked `observable`; until then the handling is
implemented per this spec and covered by fake-process tests.

## 6. ACP driver (`acp.py`)

JSON-RPC 2.0 over stdio, newline-delimited. Sequence:

1. `initialize` `{protocolVersion, clientInfo: {name: "repomesh-runner", version}, clientCapabilities: {}}`.
2. `session/new` `{cwd, mcpServers: []}` -> capture `sessionId`;
   with `resume_session_id`: `session/resume` and detect id replacement.
3. Optional `session/set_model` when request.model is set; RPC error fails the run
   (silent model fallback would misreport what executed).
4. `session/prompt` `{sessionId, prompt: [{type: "text", text}]}` — the RPC response is the
   turn terminal; `stopReason == "end_turn"` (or equivalent success value) -> candidate success.
5. Wait `quiescence_seconds` for trailing `session/update` notifications, then close stdin.

Notifications `session/update`: `agent_message_chunk` (TEXT), `tool_call` (TOOL_USE),
`tool_call_update` (TOOL_RESULT). Deliverable tracking: summary = accumulated text emitted
after the last tool call (the ACP stream has no result marker; this heuristic is documented
in code). Server-initiated `session/request_permission` is answered from `PermissionPolicy`:
ALLOW -> select the least-privileged allow option (prefer `allow_once`), DENY -> reject
option, ESCALATE -> cancel + INPUT_REQUIRED.

Provider-error promotion: keep a bounded stderr tail; if the prompt response reports success
but stderr matched a provider-failure pattern (HTTP 4xx/5xx, "API call failed"), promote to
FAILED and keep the summary empty.

## 6b. app-server driver (`app_server.py`) — codex

Protocol verified live against codex 0.145.0 on 2026-08-03; every shape below
was observed on the wire, not inferred.

Spawn: `codex app-server` (no `--listen` flag needed). Newline-delimited
JSON-RPC over stdio. **Responses omit the `jsonrpc` field** — they arrive as
`{"id": N, "result": {...}}`; match on `id` alone and never require `jsonrpc`.

Sequence:

1. `initialize` `{"clientInfo": {"name": "repomesh-runner", "version": ...}}`
   -> `result` with `userAgent`, `codexHome`, `platformFamily`.
2. Notification `initialized` `{}` (no response).
3. `thread/start` `{"cwd": <workspace>}` -> `result.thread` with:
   - `thread.id` (**not** `threadId`) — the native session id;
   - `thread.sessionId` (equal to `id` in observed runs);
   - `thread.path` — the rollout `.jsonl` transcript path, reported directly,
     so it is never derived from the id.
   Resume uses `thread/resume` with the stored thread id.
4. `turn/start` `{"threadId", "input": [{"type": "text", "text": <prompt>}]}`.

**Critical divergence from ACP: the `turn/start` response is not terminal.** It
returns immediately with `result.turn.status == "inProgress"`. The terminal
signal is the notification `turn/completed` (also `turn/failed`, `turn/aborted`
by symmetry — only `turn/completed` was observed). The driver must ignore the
RPC response as an outcome and wait for the notification, with the idle
watchdog as the only bound.

Notification vocabulary (observed):

| Notification | Mapping |
| --- | --- |
| `thread/started` | SESSION_STARTED (id + transcript path) |
| `turn/started` | LOG |
| `item/started`, `item/completed` | dispatch on `item.type` |
| `item/agentMessage/delta` | TEXT (token deltas, accumulate) |
| `item/commandExecution/outputDelta` | TOOL_RESULT stream |
| `turn/diff/updated` | LOG; carries the cumulative unified diff |
| `turn/completed` | terminal success (`turn.status == "completed"`) |
| `thread/status/changed` | LOG (`active` / `idle`) |
| `thread/tokenUsage/updated`, `account/rateLimits/updated` | LOG |
| `mcpServer/startupStatus/updated`, `remoteControl/status/changed` | LOG |

`item.type` values observed: `userMessage`, `agentMessage`, `commandExecution`,
`reasoning`, `fileChange`. Map `commandExecution`/`fileChange` starts to
TOOL_USE (watchdog `tool_started`) and their completions to TOOL_RESULT
(`tool_finished`); `reasoning` to THINKING; `agentMessage` text to TEXT.

Deliverable: accumulate `item/agentMessage/delta` text per `itemId` and take
the last completed `agentMessage` whose `phase` is not `commentary`; fall back
to the last completed `agentMessage` of any phase. `turn/diff/updated` supplies
the final diff for artifact reporting and must never be used as the summary.

Approval: codex issues approvals as server-initiated requests. None were
observed under the default local config — the agent executed PowerShell freely
— so the handler is written per the JSON-RPC pattern (respond on the request's
`id`) and is covered only by fake-process tests until a sandboxed config
reproduces one. Treat `observable=True`, `resumable=False` until resume is
verified.

Hermeticity note: the app-server auto-starts the user's configured MCP servers
(`playwright`, `node_repl`, … were observed). A Runner container must ship a
`CODEX_HOME` with no MCP servers configured, otherwise the run is not
reproducible. This is a deployment requirement, not a driver behavior.

Corrections from the implementation pass (also observed live, codex 0.145.0):

- `thread/started` carries the full `thread` object, identical to the
  `thread/start` result; adopting the id and path from either source is
  idempotent.
- Error responses are `{"error": {...}, "id": N}` — still no `jsonrpc` member.
- Client→server frames **may** include `"jsonrpc": "2.0"` (accepted), and
  unknown params fields are ignored rather than rejected.
- `thread/start` with a relative `cwd` receives **no response at all**. The
  workspace path must be absolute; the driver resolves it before sending.
- `thread/start` accepts a `model` param but does not validate it — a bogus
  value yields a normal thread plus a service-tier warning, and the effective
  model comes back as `result.model`. The driver hard-fails when the echoed
  model differs from the requested one, mirroring the ACP "never silently
  substitute a model" rule.
- `thread/resume` params `{threadId, cwd}` confirmed through its error path
  (`-32600 no rollout found for thread id …`); a successful resume is still
  unverified, so `codex` keeps `resumable=False`.
- Observed `phase` values are `final_answer` and `commentary`.
- Two further notifications land in the LOG bucket: `warning`
  (`{threadId, message}`) and `turn/started`.

## 6c. Permission enforcement reality (verified 2026-08-03)

Live testing against an authenticated claude invalidated the assumption that
per-tool permissions can be delegated to the CLI:

- Without `--permission-mode`, claude never emits `control_request` in `-p`
  mode; it silently refuses tool use and still reports success. The dynamic
  `PermissionPolicy` callback is therefore unreachable through the CLI —
  `canUseTool` is an Agent SDK capability, and this CLI build exposes no
  `--permission-prompt-tool` flag.
- With `--permission-mode acceptEdits`, tool use proceeds and the file is
  modified as intended.
- `--disallowedTools Edit,Write,MultiEdit` did **not** prevent the edit: the
  agent reached the same result through other tools (10 tool calls, file
  mutated).
- `--allowedTools Read` did **not** prevent the edit either (3 tool calls, file
  mutated).

Conclusion: for the stream-json family, CLI tool flags are advisory, not a
security boundary. Permission enforcement belongs outside the agent process —
worktree isolation, read-only context mounts, container filesystem scope, and
network egress control — which is where `runtime-planes.md` already places it.
The `PermissionPolicy` abstraction stays in the contract because ACP genuinely
delivers `session/request_permission` and codex issues approval requests, but
profiles must not claim enforcement they cannot deliver. The `claude-code`
profile keeps `permission_arguments` for mode selection only, and its
capability flags stay conservative.

### Bypass is auto-approval, never unfiltered (decision D2, 2026-08-05)

`bypass_permissions` is permitted as a mode (the 2026-08-03 decision that lifted
the prohibition stands), but it means **"do not ask"**, not **"do not filter"**.
Platform deny rules bind in every mode; bypass only removes the interactive
confirmation step. Precedence is fixed (decision D3):

```
denied_paths > disallowed_tools > allowed_paths > allowed_tools > provider mode
```

| Mode | argv effect | policy answer |
| --- | --- | --- |
| `default` | `--permission-mode default` | ESCALATE (run ends `input_required`) |
| `accept_edits` / `auto` | `--permission-mode acceptEdits` | ALLOW, minus `denied_paths`, `disallowed_tools` and off-allowlist `allowed_paths` |
| `bypass_permissions` | `--permission-mode default` (deliberately the ask-everything args) | ALLOW, still minus `denied_paths` and `disallowed_tools` |

**No profile maps a CLI's own bypass flag.** `--permission-mode
bypassPermissions`, `--yolo` and `--dangerously-bypass-approvals-and-sandbox`
all stop the CLI from emitting the permission callback — and that callback
(`control_request` / `session/request_permission` / codex approval requests) is
the only channel on which the deny rules are enforced. Mapping them would
silence the enforcement point in exactly the mode that needs it most. Platform
bypass therefore keeps the CLI in its ask-everything mode and auto-approves over
the protocol, at the cost of one round-trip per tool call.

Boundary layering, stated plainly: the protocol callback is a **cooperative**
defence (it holds only because the CLI asks and honours the answer); the **hard**
boundary is the container, filesystem and network scope around the process.
Path extraction reads string leaves of the tool input, so a path buried inside a
shell command string is not seen by the policy.

Two rules survive unchanged:

1. The mode is task state, never environment state. A Runner must not read
   `AGENTTEAMS_YOLO` or any inherited variable as a permission source —
   otherwise an AgentTeams-managed container silently overrides the task.
2. Whatever must actually hold has to be enforced below the agent: worktree
   scope, read-only context mounts, container filesystem and network limits.
   Until those exist, an autonomous run is bounded only by its workspace path.

Adjudication and the reasoning behind it: decision D2/D3 in
`docs/Bohan/Runtime/runner-execution-plane-plan.md`. This subsection supersedes
steps 2-3 of section 8 below, which still describe the pre-D2 executor (an
outright rejection of the mode, and a precedence chain without the path rules).

## 6d. Side-effect policy: the Runner never retries

`AGENTS.md` requires every external side effect to declare an idempotency key
or a retry policy. Spawning a coding CLI is such a side effect, and the policy
is deliberately empty: **a driver attempts a run exactly once.**

- No driver retries a spawn, an RPC, or a turn. A failure becomes a terminal
  `DriverResult` and the process is torn down.
- Retry, agent substitution, replanning, and abandonment are decisions for
  Task Orchestration, which owns the attempt counter (`RunnerTask.attempt`)
  and the business context needed to choose. A retry is a new task with a new
  `attempt` and a new `idempotency_key`.
- Idempotency at the boundary is provided by the engine, not the driver:
  `ExecuteRunnerTask` derives event ids from
  `(run_id, attempt, sequence, event_type)` and passes
  `"<idempotency_key>:event:<sequence>"` to the sink, so replaying a run
  produces the same event identities.

The reason for keeping this explicit: if the driver retried silently, the same
task would run twice against the same worktree — once by the driver and once by
orchestration — with no record of the first attempt.

## 7. Supervision (`supervision.py`)

- `ProcessHandle` protocol: `write_stdin(bytes)`, `close_stdin()`, async iterator of stdout
  lines, `stderr_tail() -> str` (bounded, default 8 KB), `terminate(grace_seconds)`,
  `wait() -> int`, `pid`.
- `SubprocessFactory` (real): `asyncio.create_subprocess_exec`, cwd, merged env, pipes for
  all three streams. Stderr drained continuously into the bounded tail (never blocks the
  child). Windows: `CREATE_NEW_PROCESS_GROUP`; termination via `taskkill /T /F` fallback
  after grace. POSIX: `start_new_session=True`; SIGTERM to the group, wait for the group,
  then SIGKILL to the group; only afterwards close pipes.
- Idle watchdog: fires when no protocol event arrived within the active window
  (`tool_window_seconds` while a TOOL_USE has no matching TOOL_RESULT, else
  `idle_window_seconds`). Firing terminates the process and yields TIMEOUT. No wall-clock
  cap by default.
- Fake process for tests: scripted stdout lines with optional delays, captured stdin frames,
  exit-code control — lives in `tests/runner/` as a shared fixture, not in `src/`.

## 8. Executor (`executor.py`)

`DriverExecutor(profiles, drivers, process_factory)` implements the existing
`RunnerExecutor` protocol:

1. Resolve profile by `task.adapter_id` (unknown id -> raise; engine already maps raised
   errors to a FAILED runner event).
2. Enforce `RunnerPermissions.mode != BYPASS_PERMISSIONS` (raise `UnsafePermissionMode`-like
   error; worker sessions never bypass).
3. Build `AllowlistPermissionPolicy` from `RunnerPermissions`: disallowed_tools -> DENY;
   allowed_tools non-empty and tool not in it -> mode-dependent (accept_edits/auto -> DENY,
   default -> ESCALATE); otherwise ALLOW for accept_edits/auto, ESCALATE for default.
4. Build `DriverRequest` (workspace comes from the executor's configured workspace root for
   this run — worktree creation itself is out of scope, the path is a parameter).
5. Map `DriverResult` -> `RunnerExecutionResult`:
   `SUCCEEDED -> RunnerResultStatus.SUCCEEDED` (summary passthrough),
   `FAILED/TIMEOUT -> FAILED` (summary = diagnostics),
   `INTERRUPTED -> INTERRUPTED`, `INPUT_REQUIRED -> INPUT_REQUIRED`.
   `native_session_id` passes through after sanitization.

## 9. Testing

- Driver tests use the scripted fake process only; they assert argv construction, stdin
  frames, event mapping, permission decisions (all three), fail-closed finalization, and the
  precedence chain in section 3.
- `test_smoke_real_clis.py`: `pytest.mark.skipif` on binary absence or missing
  `REPOMESH_RUNNER_SMOKE=1`. Kimi ACP smoke: initialize + session/new + trivial prompt in a
  temp dir. Claude smoke: argv shape and auth-failure classification only (no authenticated
  session assumed).
- `uv run ruff check .` and `uv run pytest` must both be green; existing tests unmodified.
