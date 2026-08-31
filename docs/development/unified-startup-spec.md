# Unified Startup And Automatic Dependency Completion

Status: implementation in progress

## Outcome

A new clone on a machine with a running Docker engine reaches the RepoMesh setup wizard through
one product launcher. RepoMesh, PostgreSQL, the web console, AgentTeams, Matrix, MinIO, migrations,
and internal service credentials are installed or wired automatically. The operator supplies only
credentials that RepoMesh cannot derive, primarily the model API key and optionally a GitHub App.

## Product Contract

The primary launchers are `scripts/start.ps1` and `scripts/start.sh`. They:

1. verify that Docker exists and its daemon is ready;
2. select unused host ports without adopting unrelated services;
3. persist generated internal credentials under `.secrets/`;
4. install AgentTeams non-interactively when its controller is absent;
5. inject Controller, Matrix, and MinIO credentials into the RepoMesh API;
6. start PostgreSQL, the full execution-plane API, and the nginx-hosted web console;
7. wait for API and web health before opening or printing the console URL;
8. remain idempotent when rerun.

`scripts/dev-up.*` remains a developer-only hot-reload path. Direct Compose commands remain an
operator and CI surface, not the primary user journey.

## Host Boundary

The product launcher may detect but must not silently install Docker. Docker installation can
require administrator privileges, virtualization changes, license acceptance, and a reboot. A
missing or stopped Docker daemon is reported as `waiting_for_user` with one concrete next action.

Host Python, uv, Node.js, npm, PostgreSQL, Matrix, MinIO, and AgentTeams are not product-launch
prerequisites. They run in containers. The developer launcher may retain its own host prerequisites.

## Dependency Model

`GET /api/v1/setup/status` retains `checks` during migration and adds `dependencies` entries:

| Field | Values | Meaning |
| --- | --- | --- |
| `state` | `checking`, `ready`, `missing`, `repairing`, `waiting_for_user`, `failed`, `optional`, `pending_onboarding` | Current observable state |
| `owner` | `system`, `user`, `onboarding` | Who can resolve it |
| `remediation` | `automatic`, `user_input`, `optional`, `workflow` | How it is resolved |
| `required` | boolean | Whether it blocks project creation |

Classification:

- system/automatic: database, AgentTeams, Matrix, internal authentication;
- user/user_input: model connection and first administrator;
- user/optional: GitHub App;
- onboarding/workflow: repositories and Agent Directory.

Business data must never be rendered as a broken platform dependency. Optional capabilities must
never block entering the console.

## Setup Wizard

The environment step groups dependencies by ownership instead of showing a flat red/green matrix.
System dependencies say that startup manages them automatically. User-input dependencies link to
their form step. Optional capabilities are explicitly skippable. Repository and Agent Directory
states belong to the onboarding step.

The browser never receives an endpoint that executes arbitrary shell commands. Host remediation is
owned by the launcher. The API reports observable state and performs only container-local actions.

## Security

- Generated secrets are gitignored and persisted across restarts.
- Secret values never appear in status responses, logs, or the setup summary.
- The web console and API share one generated action credential automatically during the current
  dual-auth transition.
- Long term, local human sessions replace the browser action token; internal tokens remain
  service-to-service only.

## Compatibility

- Existing `checks`, `counts`, `next_actions`, and `ready_for_project_creation` remain available.
- `start-platform.* --install-agentteams` remains accepted; absence now triggers installation
  automatically, while the flag forces the installer path.
- `console` Compose profile remains available until the product profile has passed clean-machine
  acceptance on Windows, Linux, and macOS.

## Acceptance

1. With Docker running and no host Python or Node.js, one launcher opens the login/setup screen.
2. Missing AgentTeams is installed without an interactive prompt.
3. Matrix, MinIO, and internal tokens become ready without user input.
4. The console and API authenticate without copying a token.
5. The only required external credential form is the model connection.
6. Port conflicts select another port and the printed/opened URL is correct.
7. Rerunning the launcher preserves accounts, credentials, and database state.
8. Missing Docker produces a clear user-owned prerequisite, not a partial installation.

## Two-Stage Model Bootstrap

AgentTeams' installer consumes the model API key before RepoMesh's browser wizard currently exists.
An empty-machine launch therefore cannot make the model form the first user-owned step by passing a
fake key or by failing before the web service starts.

The required follow-up design is:

1. start PostgreSQL, RepoMesh API, and nginx even when AgentTeams is not installed;
2. report AgentTeams/Matrix as system-owned `missing` dependencies while the model is
   `waiting_for_user`;
3. after the model credential is saved, signal a narrowly scoped bootstrap reconciler;
4. let that reconciler install AgentTeams and configure its AI gateway from the saved model
   connection, without exposing the credential in browser responses, process arguments, or logs;
5. restart/reconcile the execution plane and move AgentTeams/Matrix to `ready`.

This reconciler is not an arbitrary shell endpoint. It accepts only the predefined platform
bootstrap operation and uses a purpose-bound internal credential. Until this task is complete, the
Docker-first launcher is fully automatic when a model connection already exists in `.env`, but a
brand-new machine with no model key cannot yet finish from the browser alone.

The detailed design and executable backlog are maintained in:

- [Two-stage bootstrap specification](two-stage-bootstrap-spec.md)
- [Two-stage bootstrap P0 tasks](two-stage-bootstrap-tasks.md)

## Tasks

- [x] P0: encrypted platform credential persistence and admin credential API.
- [x] P0: automatic internal token, Matrix, and MinIO injection.
- [x] P0: automatic AgentTeams install when the controller is absent.
- [x] P0: configurable API host port and generated frontend action-token synchronization.
- [x] P0: add nginx web service to the full `platform` Compose profile.
- [x] P0: add `start.ps1` and `start.sh` product launchers with port selection.
- [x] P0: make launchers wait for and report one web URL.
- [x] P1: add the structured `dependencies` setup-status contract.
- [x] P1: render grouped automatic/user/optional/workflow states in the wizard.
- [x] P1: stop treating repositories and Agent Directory as platform failures.
- [x] P1: update README files so the product launcher is the primary path.
- [x] P1: add contract, script syntax, Compose, and frontend tests.
- [ ] P0: start the minimal web/API/database plane before a model key or AgentTeams exists.
- [ ] P0: add a purpose-bound bootstrap reconciler for AgentTeams model configuration.
- [ ] P0: trigger reconciliation after model credentials are saved and expose its progress.
- [ ] P0: validate the two-stage flow with no model key and no AgentTeams installation.
- [ ] P2: replace browser action-token authentication with the local account session.
- [ ] P2: execute clean-machine acceptance on Windows, Linux, and macOS.
