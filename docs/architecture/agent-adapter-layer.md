# Agent Adapter Layer

## Goal

The adapter layer translates RepoMesh's provider-neutral run contract into a concrete coding
agent CLI contract. It is based on the public adapter design in
[`Untrivial-ai/agent-orchestrator`](https://github.com/Untrivial-ai/agent-orchestrator/tree/10025449557665e2474c67096dab47c32d10138f) at revision `10025449557665e2474c67096dab47c32d10138f`. The reference is Apache-2.0 licensed; RepoMesh keeps its own Python contracts and implementation.

The adapter does not own process supervision, worktrees, retries, artifact collection, remote
Git writes, pull requests, or merges. Those remain in Agent Runtime and Delivery.

## Contract

Every adapter provides six operations:

1. `probe`: find the CLI and perform a short, local login-state check.
2. `build_launch`: produce executable, arguments, environment overrides, cwd, and prompt mode.
3. `session_info`: normalize a native session id captured from hooks or CLI metadata.
4. `build_restore`: produce a native resume command, or return `None` when unsupported.
5. `receive_feedback`: turn CI, review, conflict, or coordination feedback into a new prompt.
6. `manifest`: declare identity, binary names, prompt mode, capabilities, and source revision.

`unknown` authentication is a valid result. It means the installed CLI has no reliable cheap
probe or the probe could not decide; it must never be reported as logged in.

## Registered Adapters

The registry is the single source of truth for **discovery** — which CLIs
RepoMesh knows about, how to find their binary, and how to probe login state.
It is **not** the source of truth for execution.

Every entry below was transcribed from the upstream reference in
`source_revision` and describes the *interactive* invocation. Real-machine
testing on 2026-08-03 showed those shapes fail unattended: `codex` exits with
`stdin is not a terminal`, and `kimi` renders its TUI and exits 0 having done
no work. Unattended execution therefore belongs to the protocol drivers in
`src/repomesh_runner/drivers`, and each entry carries an `execution_status`:

| Status | Meaning |
| --- | --- |
| `unverified` | Never run against the real CLI. Do not execute from this shape. |
| `superseded_by_driver` | A verified Runner profile exists and is authoritative. |

Currently `claude-code`, `codex`, and `kimi` are `superseded_by_driver`; the
remaining entries are `unverified`. Contract tests keep the two registries from
drifting: adding a driver profile without marking its catalog entry fails, and
so does a profile with no catalog entry.

The 23 registered adapters: 


| Adapter id | CLI | Initial prompt | Native restore |
| --- | --- | --- | --- |
| `claude-code` | `claude` | command | `--resume` |
| `codex` | `codex` | command | `resume <id>` |
| `opencode` | `opencode` | command | `--session` |
| `grok` | `grok` | command | `-r` |
| `cursor` | `cursor-agent` | command | `--resume` |
| `qwen` | `qwen` | command | `--resume` |
| `copilot` | `copilot` | command | `--resume` |
| `kimi` | `kimi` | after start | `--session` |
| `droid` | `droid` | command | `-r` |
| `amp` | `amp` | after start | `--resume` |
| `agy` | `agy` | command | `--conversation` |
| `crush` | `crush` | after start | `--session` |
| `aider` | `aider` | after start | unsupported |
| `goose` | `goose` | after start | `run --resume --session-id` |
| `auggie` | `auggie` | command | `--resume` |
| `continue` | `cn` | command | `--fork` |
| `devin` | `devin` | command | `-r` |
| `cline` | `cline` | after start | `--id` |
| `kiro` | `kiro-cli` | command | `chat --resume-id` |
| `kilocode` | `kilocode` | command | `--session` |
| `vibe` | `vibe` | command | `--resume` |
| `pi` | `pi` | command | `--session` |
| `autohand` | `autohand` | command | `resume <id>` |

## Safety Rules

- A missing CLI returns `binary_not_found` before any runtime is created.
- A production Worker cannot use `bypass_permissions`.
- An adapter returns argv as a tuple; no shell string is constructed or evaluated.
- Environment values are passed separately and are never embedded in argv.
- Native session ids are opaque values. RepoMesh does not parse or synthesize their meaning.
- Feedback carries evidence text and links, not hidden reasoning or credentials.

## Ownership

- `modules/agent_runtime/ports/agent_adapter.py`: provider-neutral public contract.
- `integrations/coding_agents/base.py`: binary resolution, auth probe, safety, session and feedback
  normalization.
- `integrations/coding_agents/catalog.py`: 23 CLI definitions.
- `integrations/coding_agents/registry.py`: unique-id registration and lookup.
- `tests/contracts/test_agent_adapters.py`: cross-provider contract tests.

The next runtime task is to consume `LaunchPlan`, supervise the process, install provider-native
hooks, persist the session id, and deliver `AFTER_START` prompts only after readiness detection.
