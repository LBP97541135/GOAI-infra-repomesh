# Leader Actions Contract — v1

The HTTP decision surface a Repository Leader Bridge uses to receive its assignment facts and
submit its own products: Engineering Spec, task DAG, worker tasks, and the evidence-based
review verdict. Producer: RepoMesh `task_orchestration` (PR 7). Consumers: the Bridge leader
lane's `LeaderActionPort` HTTP adapter (PR 8) and both sides' test suites. Frozen as part of
the wave-0 contract baseline on 2026-08-28; changes after the freeze require a new sibling
version directory.

Design adjudications this surface embodies: D-1 (HTTP agent-actions, no leader MCP server),
D-6 (external member tokens via `REPOMESH_RUNNER_WORKER_TOKENS`, historical name kept), D-8
(the leader never receives a repository workspace — this surface carries text and structured
facts only).

## Endpoints

Same `agent-actions` surface and authentication mechanism as the existing
`POST /api/v1/agent-actions/start-worker-task`.

| Method + path | Request body | 200 response |
|---|---|---|
| `GET /api/v1/agent-actions/leader/assignments/{taskId}` | — | `repository-assignment-package.schema.json` |
| `POST /api/v1/agent-actions/leader/assignments/{taskId}/plan` | `repository-plan-decision.schema.json` | `plan-receipt.schema.json` |
| `POST /api/v1/agent-actions/leader/assignments/{taskId}/review` | `repository-review-decision.schema.json` | `review-receipt.schema.json` |

`{taskId}` is the **leader task id** and is the only place it appears — bodies deliberately do
not repeat it. Every non-2xx response body is `structured-error.schema.json`.

## Authentication and authorization

- The caller presents its own external member token (`Authorization: Bearer <token>`), the same
  credential its enrollment's `credentialRefs.repomesh` references. Server-side the token map is
  `REPOMESH_RUNNER_WORKER_TOKENS` (historical name; semantics: external member id → token).
- The server derives the caller's principal from the token — the token names the subject; no
  body or path field is trusted to describe the caller.
- The derived principal must have role `REPOSITORY_LEADER` (else 403 `forbidden_role`) and must
  be the assignee of `{taskId}` (else 403 `forbidden_not_assignee`).
- The reverse boundary is unchanged (AC-02): a leader token calling `start-worker-task` is
  still rejected by the existing worker-only hard checks. Nothing on this surface can start,
  edit, or execute a coding task directly.

## Phase state machine

```text
planning ──POST /plan (200)──► executing ──all worker tasks terminal──► review_due
   ▲                                                                        │
   └──────────── new revision worker tasks in flight ◄──request_rework──────┤
                                                            approve/escalate└──► closed
```

- `GET` is valid in every phase and always returns the full package for the current phase.
- `POST /plan` is valid only in `planning`; anywhere else it is 409 `phase_conflict`.
- `POST /review` is valid only in `review_due`; anywhere else it is 409 `phase_conflict`.
- The leader task reaches a terminal status **only** through `POST /review` (or platform-side
  cancel/supersede); there is no automatic roll-up in leader mode.

## Frozen invariants

These are the wave-0 freeze of the leader interface's behavior. The producer must enforce each
one; `tests/contracts/test_leader_actions_v1_contract.py` pins them against the shared
fixtures, and PR 7's HTTP test suite must prove them against the live implementation.

1. **Phase/evidence coupling** — `reviewEvidence` is `null` in `planning`/`executing` and
   non-null in `review_due`/`closed`. A review verdict can only ever be based on the package's
   own evidence.
2. **Idempotent repeats** — the leader task id is the plan idempotency key; the (leader task
   id, reviewRevision) pair is the review idempotency key. Identical resubmission returns the
   original receipt with 200; a *different* plan/review under the same key is 409
   `phase_conflict`, never a silent replacement.
3. **DAG validity** — nodes ↔ workerTasks correspond one-to-one via `nodeId`; every edge
   references declared nodes; the graph is acyclic (`plan_invalid_dag_cycle` /
   `plan_invalid_dag_coverage`).
4. **Envelope clamp** — every assignee ∈ workerRoster (`plan_invalid_assignee`); every
   `allowedPaths` entry under some `safetyEnvelope.allowedPathRoots` root
   (`plan_invalid_allowed_paths`); every worker task's `tests` ⊇
   `safetyEnvelope.testCommands` (`plan_invalid_tests_removed`).
5. **Provenance and rework revision** — plan and review must carry
   `source: "leader-codex-session"` provenance (`plan_invalid_provenance`); `request_rework`
   requires ≥ 1 finding with `reworkInstruction` (`review_invalid_findings`), creates new
   revision worker tasks through the formal assignment path, never mutates already-terminal
   worker tasks, and increments `reviewRevision` for the next round.

## Error matrix

Frozen in `fixtures/error-matrix.json` (machine-checked against the schema enum):

| HTTP | codes |
|---|---|
| 401 | `invalid_token` |
| 403 | `forbidden_not_assignee`, `forbidden_role` |
| 404 | `assignment_not_found` |
| 409 | `phase_conflict`, `decomposition_mode_conflict`, `plan_invalid_dag_cycle`, `plan_invalid_dag_coverage`, `plan_invalid_assignee`, `plan_invalid_allowed_paths`, `plan_invalid_tests_removed`, `plan_invalid_provenance`, `review_invalid_findings` |

`decomposition_mode_conflict` (409) fires when the assignment's team is in `server`
decomposition mode: the surface exists, but that team's planning is done server-side, so
leader submissions are rejected rather than half-applied.

## Fixtures

`fixtures/` is the single shared source for both producer and consumer test suites. The valid
fixtures form one coherent scenario (one leader task on the pricing-core repository, a
single-worker roster, a two-node DAG) so cross-document invariants are checkable, not merely
per-document shape:

| File | Meaning |
|---|---|
| `assignment-package.planning.json` | planning-phase package, `reviewEvidence: null` |
| `assignment-package.review-due.json` | review_due package with full worker evidence |
| `plan-decision.valid.json` | consistent with the planning package: roster assignee, in-envelope paths, envelope tests kept, acyclic covering DAG |
| `plan-decision.invalid-dag-cycle.json` | must be 409 `plan_invalid_dag_cycle` |
| `plan-decision.invalid-assignee.json` | must be 409 `plan_invalid_assignee` |
| `review-decision.approve.json` | approve verdict over the review_due evidence |
| `review-decision.request-rework.json` | rework verdict with a reworkInstruction finding |
| `plan-receipt.json` | receipt for the valid plan |
| `review-receipt.approve.json` | receipt for the approve decision |
| `error-matrix.json` | frozen status → code mapping |
| `error.401.invalid-token.json` … `error.409.plan-invalid-dag-cycle.json` | one sample structured error per status class |
