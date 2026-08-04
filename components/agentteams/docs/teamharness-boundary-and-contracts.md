# TeamHarness v0.1 Boundary And Contracts

This document defines the functional boundary for the current TeamHarness
plugin package.

TeamHarness v0.1 is the runtime-neutral team collaboration base. It packages
stable prompts, team skills, MCP tools, lifecycle scripts, and runtime adapter
entrypoints. It does not own worker lifecycle, controller reconciliation,
worker desired-state apply loop, runtime hook behavior, or
periodic workspace persistence.

## Responsibilities

TeamHarness owns:

- Stable team collaboration prompts and role prompts.
- Team collaboration skills for communication, shared files, rooms, team
  coordination, projects, task delegation, and task execution.
- Explicit MCP tools for team messages, inbound room events, room flow, shared
  files, room artifacts, project flow, task flow, and plugin health.
- A single plugin tarball installed through the AgentTeams `agt` CLI by
  default and compatible with LoongSuite `plugin-probe` for local runtimes.

TeamHarness does not own:

- Controller generation of `agents/{runtimeName}/runtime/runtime.yaml`.
- Worker process lifecycle, pod restart, or runtime process supervision.
- Worker desired-state parsing, polling, apply, or diagnostics.
- Runtime-neutral top-level hooks.
- Runtime hook trigger contracts, payload formats, and enforcement behavior.
- Credential access enforcement that depends on runtime-specific file or tool
  guard support.
- AgentSpec package download, apply, rollback, or update inside the worker desired-state apply loop.
- Periodic workspace push/pull loops.
- Direct QwenPaw or Claude Code runtime mutation in the base package.
- Secret value storage.

## Contract Relationships

Controller to runtime:

- The controller writes non-secret desired state and team facts to
  `agents/{runtimeName}/runtime/runtime.yaml`.
- Secrets stay in environment variables, mounted files, or service account
  tokens.

Runtime worker to TeamHarness:

- The worker installs or exposes TeamHarness assets for the selected runtime.
- The worker owns the desired-state apply loop, including runtime config polling and AgentSpec package application.
- The worker may call TeamHarness MCP tools, but TeamHarness does not poll CR
  state or object storage by itself.

Runtime adapter to TeamHarness:

- The adapter maps TeamHarness prompts, skills, and MCP into a concrete runtime.
- Runtime-specific hooks live under the adapter implementation, for example
  `adapters/qwenpaw/hooks/`, when that runtime integration phase defines them.
- Runtime config consumption belongs to the worker/runtime adapter layer, not to
  the TeamHarness plugin package.
- The adapter should consume controller-written runtime config facts instead of
  querying the `agt` CLI for team or member identity.

Remote member to TeamHarness:

- A remote member is a Worker with `spec.containerManaged: false`. The
  controller provisions its Matrix identity, rooms, and storage prefixes but
  starts no container; the process runs on an operator's own machine under the
  operator's own runtime login.
- The member consumes TeamHarness through the MCP tools and projected assets
  only. It does not receive the worker desired-state apply loop, the runtime
  hook contract, or credential guard enforcement.

TeamHarness plugin package to AgentSpec package:

- The TeamHarness plugin package is runtime infrastructure.
- `desired.agentPackage` in `runtime.yaml` is an AgentTeams AgentSpec package and
  belongs to the worker desired-state apply path.
- Updating an AgentSpec package must not be modeled as updating the TeamHarness
  plugin package.

## Credential Eligibility

A member with `spec.containerManaged: false` is **not eligible** for sensitive
credential bindings. Concretely, the controller and the operator must not
assign such a member:

- `spec.credentialBindings` — runtime credential references resolved through
  `spec.agentIdentity`.
- `config/credagent.json` protected credentials — the CredAgent file guard and
  output sanitization rules described in `docs/credagent-config.md`.
- Any cloud credential scope from `spec.accessEntries` beyond what the member
  already needs for its own room and workspace prefix.

This is a declared boundary, not a gap to close later. The enforcement it would
depend on is structurally unavailable off-container:

- A managed runtime enforces credential protection **in process**. The qwenpaw
  adapter is a Python plugin loaded by the runtime itself:
  `apply_credential_guard()` mutates the live file guard, and
  `install_output_sanitizer_wrapper()` wraps `QwenPawAgent._acting` so every
  tool result passes `sanitize_tool_result()` before the agent sees it. Both
  depend on sharing an address space with the runtime, so there is no path
  around them — and no way to reproduce them from outside the process.
- An external coding CLI has no such extension point. Claude Code can only
  approximate the file guard with `PreToolUse` / `PostToolUse` hooks and
  `permissions.deny`, which cover the tools the runtime routes through hooks and
  not, for example, a subprocess the agent spawns. Codex has no equivalent
  mechanism at all. Output sanitization has no approximation in either: nothing
  sits between the tool result and the model.
- The host is outside the platform's blast radius regardless. It is the
  operator's own machine, holding the operator's own runtime login, with no
  container boundary, no controller-managed process lifecycle, and no
  guarantee about what else runs there.

Treating hooks as equivalent to in-process enforcement would mean granting a
binding on the strength of a control that does not actually hold. Declaring
ineligibility keeps the failure mode a refused assignment rather than a silently
unprotected credential.

Correspondingly, a remote member authenticates its own runtime. The bridge never
reads, copies, packages, logs, or uploads runtime credential files
(`~/.claude/.credentials.json`, `$CODEX_HOME/auth.json`); the operator logs in.
Platform credentials the member does need — Matrix token, gateway key — stay in
environment variables and are referenced by variable **name** in any projected
config.

The member's own scoped object-storage credentials fall under that last
sentence, not under the ineligibility above. In embedded mode the provisioner
already creates a per-member MinIO user and attaches a prefix-scoped policy
during identity provisioning, before container reconciliation is skipped, so
these credentials exist for a remote member for the same reason its Matrix
identity does. They are delivered the same way as the Matrix token: environment
variables on the operator's host, referenced by name. What ineligibility rules
out is credentials whose protection would depend on in-process enforcement, not
a member's own least-privilege access to its own prefixes.

In cloud or in-cluster mode there is no equivalent path: storage credentials are
issued through `POST /api/v1/credentials/sts` against a Kubernetes-authenticated
caller identity, which a host process outside the cluster does not have. Shared
storage for remote members is therefore an embedded-mode capability, and its
absence elsewhere is declared rather than treated as a defect.

## Standard Asset Set

Prompts:

- `prompts/team/TEAMS.md`
- `prompts/agent/leader.md`
- `prompts/agent/worker.md`
- `prompts/agent/remote-member.md`
- `prompts/manager/AGENTS.md`
- `prompts/manager/TOOLS.md`
- `prompts/manager/HEARTBEAT.md`

Skills:

As declared in `plugins/teamharness/plugin.yaml`, which is authoritative for
what a runtime actually installs.

- Agent skills: `mcporter`, `find-skills`.
- Team skills: `communication`, `file-sharing`, `roomflow`,
  `team-coordination`, `project-management`, `task-delegation`,
  `task-execution`.

MCP tools:

- `health`
- `message`
- `inbox`
- `roomflow`
- `filesync`
- `artifact`
- `projectflow`
- `taskflow`
