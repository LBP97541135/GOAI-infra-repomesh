# Delivery v2 Implementation Plan

## Objective

Move RepoMesh from a synchronous GitHub delivery path to a replayable multi-repository delivery
controller. GitHub API success means only that a command was accepted. RepoMesh advances durable
delivery state only after a persisted SCM observation confirms the remote fact.

## Ownership and boundaries

- `modules/delivery` owns ChangeSets, candidate revisions, observation lifecycle, merge order,
  governance facts, and rollback Saga state.
- `integrations/scm` acquires provider facts and executes provider commands. It does not own
  delivery truth.
- `modules/review_validation` owns validation snapshots, `environment_hash`, expiry, and internal
  review evidence.
- `modules/task_orchestration` owns rework Task and ExecutionPlan attempts.
- Coding agents never receive Push, PR, Merge, or Revert credentials.

Cross-module imports target published `contracts.py` only.

## Target control loop

```text
GitHub Webhook/Poller
  -> append SCMObservation
  -> claim observation with a replay lease
  -> project the fact into ChangeSet state
  -> evaluate validation, review, governance and dependency gates
  -> append an idempotent SCM command
  -> dispatch command to GitHub
  -> wait for a new SCMObservation before advancing
```

## Target state

ChangeSet:

```text
preparing <-> blocked
preparing|blocked -> ready -> merging -> merged
merging -> rolling_back -> rolled_back
preparing|ready|blocked -> rolled_back
any inconsistent external fact -> manual_intervention
```

Repository delivery uses separate axes:

- `gate_status`: `waiting_candidate`, `waiting_ci`, `waiting_review`, `ready`, `blocked`, `stale`.
- `merge_status`: `not_merged`, `merge_requested`, `merged`, `revert_requested`, `reverted`.

## Durable data

| Table | Responsibility |
| --- | --- |
| `delivery.change_sets` | ChangeSet state, merge cursor, rollback link and version |
| `delivery.change_set_repositories` | Per-repository gate and merge state |
| `delivery.candidate_revisions` | Append-only candidate SHA and Task/Run history |
| `delivery.scm_observations` | Append-only provider facts and replay progress |
| `delivery.scm_commands` | Idempotent Push/PR/Merge/Revert command journal |
| `delivery.merge_decisions` | Head-bound governance authorization |
| `delivery.recovery_plans` | Rollback Saga state |
| `delivery.recovery_actions` | Revert, PR close and revalidation actions |

## Stable delivery identity

Persist the ChangeSet before publishing candidate branches. Use:

```text
repomesh/deliver/<change_set_id>/<repository_id>
```

A rework creates a new `CandidateRevision` on the same fast-forward-only branch and PR. The new
head invalidates older CI, review, governance, and validation evidence. Failed Tasks and
ExecutionPlans remain terminal; the long-lived ChangeSet requests a new attempt through the
`CreateCIReworkTask` contract.

## Delivery slices

### PR-1: SCM observation foundation - implemented in this branch

- Provider-neutral Observation contracts live in Delivery.
- External identity is unique by provider, source, and external ID.
- Raw JSON, payload SHA-256, routing hints, attempts, error evidence and timestamps are durable.
- Processing uses optimistic versions, a five-minute lease and bounded retries.
- Signed GitHub Webhooks persist before domain processing.
- Failed and interrupted observations remain replayable after restart.
- Unsupported signed GitHub events are retained and marked processed/ignored.

### PR-2: Observation acquisition - implemented

- Add GitHub Poller snapshots for PR, Check Run, Review and Merge facts.
- Add pagination, content-addressed fact deduplication, rate-limit backoff and durable cursors.
- Feed Poller and Webhook through the same Observation service.
- Detect unauthorized merge, closed delivery PRs and branch-head drift as durable failed facts.

GitHub conditional requests remain a provider-level optimization: correctness is provided by the
durable cursor and content-addressed Observation identities, so a missing or discarded ETag cannot
skip a fact.

### PR-3: Merge command cursor - implemented

Repository delivery persists `merge_requested` and waits for a later GitHub PR observation before
recording `merged`. Merge side effects flow through the durable `scm_commands` journal and a
lease-based dispatcher. ChangeSet persists a merge cursor and refuses to release later candidates
until earlier merge orders are confirmed.

- Split gate and merge status.
- Add `scm_commands` and an outbox-style dispatcher.
- Record `merge_requested`; do not record `merged` from the command response.
- Advance `merge_cursor` only from a Merge Observation with the expected head SHA.
- Pause and alert when GitHub reports an out-of-order or unauthorized merge.

### PR-4: Governance - implemented

- Add head-bound `READY`, `BLOCKED`, and `ROLLBACK_REQUIRED` decisions.
- Require GitHub approval, stale-review dismissal, and an active governance decision.
- Preflight required GitHub branch protection/ruleset configuration.

Automatic delivery requires a current decision for the exact repository candidate SHA. GitHub
branch protection preflight verifies named checks, approval count and stale-review dismissal before
RepoMesh opens the delivery PR.

### PR-5: Stable rework identity - implemented

- Initiate the ChangeSet before branch publication.
- Add append-only Candidate Revisions.
- Implement `CreateCIReworkTask` through Task Orchestration contracts.
- Update the existing PR with fast-forward-only candidate commits.

Every ChangeSet now carries append-only Candidate Revision history. A rework keeps the ChangeSet,
delivery branch and PR number, binds the new head to a new Task, and clears CI, review and
governance evidence from the previous head. `CIReworkTaskCreator` creates the Worker assignment
through the Task Orchestration contract with a stable idempotency key.

### PR-6: Validation environment identity - implemented

- Implement immutable Review and Validation snapshots.
- Add canonical `environment_hash` inputs and configurable expiry.
- Reject stale validation at every merge gate evaluation.

Completed plans now produce an immutable Validation Snapshot from Runner test evidence before the
ChangeSet is created. The snapshot binds every repository Head, test command/result, optional Spec
version, review evidence, canonical environment hash and expiry time. Automatic merge evaluates it
again on every gate check; a rework Head therefore invalidates the previous snapshot automatically.

### PR-7: Rollback Saga - orchestration implemented

- Execute reverse-order Revert PRs without force-resetting shared history.
- Require CI, review, governance and Merge Observation for each Revert.
- Dispatch revert conflicts to a Worker Task.
- Finish as `rolled_back` or `manual_intervention` with complete evidence.

Recovery plans now execute as a restart-safe, strictly ordered Saga. Actions are planned in reverse
merge order and only one action advances per pass. Revert conflicts enter `waiting_worker`, create a
Worker repair task through the conflict gateway, and prevent later actions from running. Provider
implementations use `RevertDeliveryGateway`; the live GitHub implementation and acceptance evidence
are part of PR-8 composition.

### PR-8: Live acceptance

- Run the real composition root with PostgreSQL, AgentTeams, Runner and a GitHub App.
- Validate success, rework, duplicate/out-of-order events, process restart, unauthorized merge,
  partial merge and reverse rollback across at least two repositories.
- Retain GitHub request IDs, observations, database rows and final SHAs as acceptance evidence.

## Required quality gates

Every slice must include behavior, contract, persistence and replay tests. Every external side
effect requires an idempotency key or explicit retry policy. Before Push, run `ruff check .`,
`pytest`, and verify one Alembic head.
