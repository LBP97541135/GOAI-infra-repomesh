# ADR 0003: Runner Executes Coding Agents Through Protocol-Family Drivers

- Status: Accepted
- Date: 2026-08-03
- Extends: ADR 0002 (RepoMesh Runner plane)

## Context

The adapter catalog under `integrations/coding_agents` declares 23 CLI adapters as flat
`AdapterSpec` flag tables and produces a `LaunchPlan`, but nothing executes that plan. A
real-machine verification on 2026-08-03 (claude-code, codex, kimi installed) showed that the
declared launch shape models the interactive TUI invocation of each CLI and fails when actually
spawned:

- `codex` exits immediately with `stdin is not a terminal`.
- `kimi` starts its TUI, receives the prompt into the composer, and exits 0 with zero changes
  when stdin closes — a false success the engine would report as `runner.completed`.
- Headless invocation is a structurally different CLI surface, not an extra flag: `codex exec`
  rejects `--ask-for-approval` (the flag our permission mapping emits) and uses `--sandbox`;
  `kimi -p` rejects `--yolo`/`--auto`.

Research into three orchestrators that already drive many coding CLIs converged on the same
findings:

- AgentTeams (embedded, `components/agentteams`): no worker runtime spawns a coding CLI; the
  only existing pattern is a Manager-side alpha skill using headless `-p` argv calls with
  `timeout` and log tee. No PTY anywhere in the codebase.
- Multica (`multica-ai/multica`, Go server + daemon): fully headless, no PTY. Three protocol
  families cover 18 providers: NDJSON stream-json over stdin/stdout (claude family), ACP
  JSON-RPC over stdio (7 providers including kimi), and the codex `app-server` JSON-RPC
  surface. Terminal-state detection is fail-closed: a clean process exit without a terminal
  protocol event is a failure, and failed runs surface no output.
- Orca (`stablyai/orca`, Electron): drives interactive TUIs under node-pty and answers
  approval menus by writing keystrokes. That path depends on rendered-UI signals and a human
  fallback; it is unusable inside an unattended AgentTeams Worker.

Multica also demonstrates the cost of skipping a family abstraction: with one file per CLI and
no shared protocol driver, its codex backend alone grew to 121 KB.

## Decision

The RepoMesh Runner executes coding agents exclusively through headless protocol drivers. A
driver implements one wire protocol once; a per-CLI profile contributes only declarative
differences (binary names, argument construction, event field mapping, session-id extraction,
permission vocabulary).

1. Protocol families, in delivery order:
   - `stream-json`: NDJSON over stdin/stdout (claude; later codebuddy, qwen, cursor).
   - `acp`: Agent Client Protocol, JSON-RPC 2.0 over stdio (kimi; later hermes, kiro, qoder,
     trae, grok).
   - `app-server`: codex JSON-RPC thread API over stdio. Unlike ACP, the `turn/start` response
     is not terminal — the terminal signal is the `turn/completed` notification, so a driver
     that treats the response as the outcome reports instant false success.
   - `one-shot`: single JSON envelope on exit for CLIs without a streaming surface.
2. No PTY, no interactive TUI automation. A CLI with no headless surface is not integrated
   until it grows one.
3. Fail-closed terminal states: a run is `succeeded` only when the protocol emitted its
   terminal success event. Process exit code 0 alone is never success. Failed runs report
   diagnostics, never partial output as deliverable.
4. Permission requests arriving over the protocol (ACP `session/request_permission`, codex
   server-initiated approval requests) are answered by a Runner-side policy derived from the
   RunnerTask permissions (and later the VisibilitySnapshot) — approve, deny, or terminate as
   `input_required`. `bypass_permissions` remains forbidden for worker sessions. This
   deliberately diverges from Multica, which bypasses permissions globally; policy-driven
   answering is the RepoMesh differentiator and the hook where context visibility is enforced.

   Amended 2026-08-03 after live verification: the claude CLI does not expose this callback
   (`canUseTool` is an Agent SDK capability; the CLI has no `--permission-prompt-tool`), and
   its `--allowedTools` / `--disallowedTools` flags were both demonstrably bypassed by the
   agent reaching the same effect through other tools. CLI tool flags are therefore treated as
   advisory, never as a security boundary. Enforcement that must hold belongs outside the agent
   process — worktree isolation, read-only context mounts, container filesystem scope, and
   network egress control — consistent with `runtime-planes.md`. Profiles must not advertise
   enforcement they cannot deliver. See spec section 6c for the recorded evidence.

   Amended again 2026-08-03 by product decision: `bypass_permissions` and provider YOLO flags
   are permitted, for worker sessions included. The prior prohibition rested on the assumption
   that non-bypass modes constrained the agent; the measurements above showed they do not, so
   the ban delivered ceremony rather than containment while blocking legitimate autonomous
   runs. `BYPASS_PERMISSIONS` now maps to each provider's native flag
   (`--permission-mode bypassPermissions`, `--yolo`,
   `--dangerously-bypass-approvals-and-sandbox`) and makes the permission policy answer ALLOW
   unconditionally, including for tools listed in `disallowed_tools`. Containment for every
   mode is the workspace, container, and network scope around the process. A run's declared
   mode remains recorded for audit.

   Corrected 2026-08-05 (decision D2/D3 in
   `docs/Bohan/Runtime/runner-execution-plane-plan.md`). The amendment above stands on one
   point and is wrong on two. It stands in permitting `bypass_permissions` for worker
   sessions. It is wrong in mapping the mode onto each provider's native bypass flag, and in
   making the policy answer ALLOW unconditionally.

   The flaw is that the two are coupled: a CLI launched with `--permission-mode
   bypassPermissions` / `--yolo` / `--dangerously-bypass-approvals-and-sandbox` stops emitting
   the permission callback altogether, and that callback is the only place a Runner-side rule
   can be applied at all. The amendment therefore did not merely widen what the policy allows
   — it removed the policy from the run. The 2026-08-03 measurements showed that CLI tool
   *flags* are advisory; they did not show that the *callback* is, and on ACP and app-server
   the callback is a genuine protocol request the agent waits on.

   Adjudicated semantics, in force:

   - Platform `bypass_permissions` means auto-approval **over the protocol**, not the absence
     of filtering. It removes the interactive confirmation step and nothing else.
   - `denied_paths` and `disallowed_tools` are answered DENY in **every** mode, bypass
     included. Precedence:
     `denied_paths > disallowed_tools > allowed_paths > allowed_tools > provider mode`.
   - No profile maps a CLI's own bypass flag, ever. `claude-code` maps
     `BYPASS_PERMISSIONS` to the same ask-everything arguments as `DEFAULT`; `kimi` and
     `codex` map no permission arguments at all.
   - The boundary is layered and the layers are not equivalent: the protocol callback is a
     **cooperative** defence, honoured only because the CLI asks and respects the answer; the
     **hard** boundary remains worktree isolation, container filesystem scope and network
     egress control. Neither substitutes for the other, and the cooperative layer must not be
     switched off just because it is cooperative.

   Runner processes must not honor an inherited `AGENTTEAMS_YOLO` (or equivalent) environment
   variable as a permission source: the mode comes from the RunnerTask, so the runtime
   environment can never silently widen or narrow what the task declared.
5. Capabilities are three independent sets — launchable, observable, resumable — declared per
   profile and never assumed. The current blanket `RESTORE` capability claim is withdrawn
   until a driver proves resume per CLI.
6. Native session ids are captured from protocol events, sanitized (no leading `-`, no control
   characters, bounded length), and reported with the transcript path when the protocol
   provides one; paths are never derived from ids.

Process supervision follows the verified Multica rules: prompt bytes are written from an
independent task while stdout is drained (the claude banner deadlock); stdin stays open for
protocol back-frames; termination signals the process group, waits for the whole group, then
SIGKILLs before closing pipes; inactivity is bounded by an idle watchdog with a larger budget
while a tool call is in flight, instead of a wall-clock cap; stderr keeps a bounded tail that
joins the failure diagnostics.

## Consequences

- `src/repomesh_runner/` gains a `drivers/` package (contract, families, supervision) and a
  profile registry; the `RunnerExecutor` port gets its first real implementation.
- The `integrations/coding_agents` catalog remains the control-plane surface (probe, manifest,
  restore planning) but its launch shapes are superseded for execution; profiles become the
  execution source of truth and the catalog will be reconciled against them.
- Per-CLI code shrinks to data plus small hooks; new CLI support in an existing family is a
  profile entry plus contract tests, not a new driver.
- Contract tests validate driver behavior against scripted fake processes; a separate
  real-machine smoke suite runs only against CLIs actually installed and skips otherwise.
- ACP delivers the largest coverage per unit of work and is implemented before the codex
  app-server family.
