---
name: delivery-governance
description: Decide whether a multi-repository ChangeSet is ready for delivery. Use when an Organization Leader evaluates repository approvals, CI, validation snapshots, dependency order, rollback readiness, and human gates before release or rollback.
---

# Delivery Governance

Govern ChangeSet readiness across repositories. This Skill records the delivery decision; SCM merge
execution remains owned by delivery adapters and approved policies.

## Inputs

- ChangeSet id, repository PR observations, candidate SHAs and merge order.
- Repository Leader review decisions and validation snapshots.
- Required CI checks, human approvals, risk classification and rollback plan.
- Open blockers, contract changes and dependency status.

## Outputs

- Governance decision: `READY`, `BLOCKED` or `ROLLBACK_REQUIRED`.
- Required follow-up tasks or approvals.
- Delivery evidence bundle with checks, reviews, trace ids and rollback reference.

## Workflow

1. Verify every repository candidate matches an accepted Worker commit and validation snapshot.
2. Check required CI, approvals, contract gates and dependency order.
3. Confirm rollback or compensation path for high-risk changes.
4. Record the decision with an idempotency key tied to the ChangeSet and head SHAs.
5. Trigger delivery services only after `READY`.

## Dependencies

- Delivery, review-validation, task-orchestration and observability records.
- SCM and CI observations from configured integrations.
- Human supervision policy for approval and rollback gates.

## Failure Handling

- Use `BLOCKED` when evidence is incomplete, stale or red.
- Use `ROLLBACK_REQUIRED` when already-released changes fail validation.
- Create follow-up Worker tasks for implementation defects instead of editing candidates directly.

## Safety

- Do not merge PRs directly from this Skill.
- Do not override red CI, missing approval or stale validation evidence.
- Do not treat chat acknowledgement as a governance approval.

## Validation

- The ChangeSet references immutable candidate SHAs.
- Required checks and reviews are green for the same SHAs.
- Rollback evidence exists before risky delivery proceeds.

## AgentTeams Mapping

The Organization Leader uses this Skill after Repository Leaders accept their repository results.
AgentTeams carries coordination, while RepoMesh delivery state remains authoritative.

