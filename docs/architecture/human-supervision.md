# Human supervision and project execution modes

RepoMesh separates automatic Agent execution from human authority. A human is an external
principal with a project grant, not an AgentTeams Worker impersonation.

## Project modes

| Mode | Behavior |
| --- | --- |
| `auto` | Existing behavior. No human checkpoint is required. |
| `supervised` | Agents continue writing automatically, but configured checkpoints block progress until approved. |
| `manual_controlled` | Intended for projects where every configured critical transition is human-controlled. |

`manual_controlled` is enforced by the domain model: all six checkpoints must be present. A partial
checkpoint set is rejected instead of silently weakening the mode.

`required_checkpoints` selects any of `repository_scope`, `specification`, `execution`,
`validation`, `delivery`, and `exception_escalation`. Non-automatic projects must configure at least one human grant and
one checkpoint. This keeps `auto` backward compatible and prevents a nominally supervised project
from silently behaving as automatic.

`exception_escalation` is mandatory for every non-automatic project even if it is accidentally
omitted from `required_checkpoints`. Worker blockers first go to their Repository Leader. A human
review is created only when that Repository Leader reports the repository task as `blocked` or
`failed` to the Organization Leader, preserving the no-skip-level communication rule.

## Human grants

Each grant contains:

- `human_principal_id`: identity supplied by the future login/identity provider;
- `role`: organization, project, or repository supervisor;
- `repository_id` and `path_patterns`: optional repository and path scope;
- `code_access`: `none`, `read`, or `write`;
- `control_actions`: view decisions, approve, request changes, pause, resume, cancel, or edit spec.

Code access and control authority are evaluated independently. For example, a reviewer may approve
a delivery and inspect its diff with `read`, while remaining unable to edit the repository. A human
may receive separate grants for multiple repositories. Authorization succeeds only when one grant
covers the complete requested action, repository, path, and code access level.

## Checkpoint decisions

A decision is `approved`, `rejected`, or `changes_requested` and records the human principal,
reason, repository scope, timestamp, and `evidence_version`. Approval is valid only for the exact
evidence version:

- execution: `task:<task-id>:v<task-version>`;
- delivery: `execution-plan:<plan-id>:v<plan-version>`;
- specification and other checkpoints use their immutable version identifier.

Changing the task, plan, specification, contract, or candidate evidence makes the previous decision
stale and closes the gate. The decision history is append-only and therefore suitable for audit.

The decision API accepts only `review_request_id`, decision and reason. Project, checkpoint,
repository and evidence version are loaded from the persisted pending review and are never trusted
from browser input. A database uniqueness constraint allows exactly one decision per review; a
second or concurrent submission returns HTTP 409.

## Runtime integration

- `SpecificationService.publish_to_context` evaluates `specification` before publishing a project
  or repository specification into shared context.
- `PlanExecutionBridge.materialize` evaluates `repository_scope` before creating repository tasks.
- `TaskOrchestrator.start` evaluates the repository-scoped `execution` checkpoint before a Worker
  starts coding.
- `PlanDeliveryFinalizer.handle` evaluates `validation` and then `delivery` against the same
  immutable delivery evidence before creating a ChangeSet or publishing PRs.
- `TaskOrchestrator.report` evaluates the repository-scoped `exception_escalation` checkpoint when
  a Repository Leader reports `blocked` or `failed` to the Organization Leader. The task remains
  blocked or failed while the review is pending.
- An exception decision is returned to the Organization Leader as a structured
  `repomesh.human-decision.v1` collaboration message. Workers still communicate only with their
  Repository Leader; no skip-level room is introduced.
- Automatic projects pass all checks without a human decision, preserving the autonomous flow.

## Project lifecycle control

A granted supervisor can pause, resume, or cancel a project through
`POST /api/v1/projects/{project_id}/control`. Paused projects reject new task assignment and Agent
execution until resumed. Cancelled projects reject all further execution and cannot be resumed.
The status is persisted as `active`, `paused`, or `cancelled`, so process restarts cannot silently
remove the control decision.

The control plane reads `/api/v1/agents` and binds projects to existing AgentTeams identities. The
organization Leader, repository Leaders, and Workers are selected from this directory; the browser
does not invent Agent identifiers.

## Local account authentication

The first version uses RepoMesh local accounts. `/api/v1/auth/bootstrap` creates exactly the first
administrator, after which administrators create reviewer accounts through `/api/v1/auth/accounts`.
Passwords are stored as independently salted `scrypt` hashes. Login returns an opaque short-lived
Bearer token and also sets it in an HttpOnly, same-site session cookie for the browser; only its
SHA-256 hash is persisted. Logout removes the server-side session and clears the cookie.

Project topology creation and checkpoint decisions use the authenticated account. The checkpoint
endpoint never accepts `human_principal_id` from the request body, so a reviewer cannot impersonate
another grant holder. Session lifetime is configured with `REPOMESH_LOCAL_SESSION_TTL_SECONDS`.

## Approval inbox and push delivery

When an Agent evaluates a configured checkpoint without a valid exact-version decision, RepoMesh
idempotently creates one `human_review_requests` record. The identity is project + checkpoint +
repository scope + evidence version, so Agent retries do not duplicate a review. A new evidence
version creates a new review and cannot inherit an old approval.

`GET /api/v1/review-requests/events` provides a cookie-authenticated server-sent event stream. The
browser receives pending reviews automatically while the persisted inbox remains the recovery path
after refresh or disconnect. Repository supervisors only receive requests covered by their grant;
local administrators may inspect the whole queue. Recording a decision resolves the matching inbox
item and appends the auditable checkpoint decision.

AgentTeams delivery uses the persisted collaboration-message queue. A transport failure marks the
message `failed`; the background retry worker sends it again with the original idempotency key.
Consequently, an accepted human decision is not lost when Matrix is temporarily unavailable and a
successful retry cannot duplicate the message.

## Control-plane UI

The React/Vite application in `web/` includes first-admin setup and login, project mode/checkpoint
configuration, local reviewer management, and the live approval workbench. Run it locally with:

```powershell
cd web
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8000`, keeping session cookies same-origin during local
development. Before an internet-facing production deployment, add login rate limiting, password
reset, account disable operations, HTTPS-only cookies, CSRF protection and optional enterprise OIDC.
