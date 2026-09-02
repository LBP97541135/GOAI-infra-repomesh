# Manager Database Task Automation Specification

Status: accepted for implementation

## Decision

The Repository Manager declares database impact while authoring each Worker Task. The Worker
receives that requirement with the assignment and supplies the requested migration, backfill, and
test artifacts. RepoMesh, not the Worker, decides whether the candidate may advance and whether a
database Branch validation must run.

## Task Declaration

Every newly authored Worker Task carries `databaseChange`:

```json
{
  "declared": true,
  "required": true,
  "changeKinds": ["schema", "migration", "backfill"],
  "affectedTables": ["users"],
  "migrationRequired": true,
  "backfillRequired": true,
  "requiredChecks": ["migration_apply", "historical_data", "backfill_idempotency"]
}
```

`declared=true, required=false` is an explicit Manager decision that the Task has no database
impact. A legacy plan with no field becomes `declared=false`; it is not equivalent to an explicit
negative decision.

## Automatic Flow

```text
Manager plan accepted
-> Worker Task persists database requirement
-> assignment/spec presents the same requirement
-> Worker produces candidate and structured evidence
-> RepoMesh cross-checks Manager requirement, evidence, and Git diff
-> required + complete => database Branch validation requested automatically
-> undeclared database diff => return to Manager
-> missing artifact/check => return to Worker
-> passed Branch evidence + same commit => Validation Snapshot and Merge Gate
```

## Authority

- Manager may declare scope and required checks only inside the repository assignment envelope.
- Worker may create code, migrations, backfills, and tests, but cannot change the persisted Task
  declaration or mark Branch validation passed.
- RepoMesh adds mandatory checks and fails closed on contradictions.
- Human review handles exceptional waivers; no Worker-authored text is a waiver.

## Trigger Decision

A database validation trigger is `ready` only when:

- the Task declaration is explicit and `required=true`;
- the Task succeeded with a candidate commit;
- structured Worker database evidence exists;
- every required migration/backfill artifact is present;
- every Manager-required check has passing execution evidence;
- Git diff contains no undeclared database artifact.

Otherwise the decision is `not_required`, `manager_review`, or `worker_rework`, with stable reasons.

## Compatibility

The existing leader-actions v1 reader accepts plans without `databaseChange` as legacy
`declared=false`. New Manager prompts and fixtures emit the field. Once all deployed Bridges
support it, a v2 contract will make the field structurally required and v1 will be read-only.

## Acceptance

- Manager declaration round-trips through plan -> Task -> PostgreSQL -> TaskView;
- assignment/package exposes the immutable declaration to the Worker;
- explicit no-change differs from legacy undeclared;
- required migration/backfill/check omissions produce Worker rework reasons;
- undeclared database diff produces Manager review, never automatic delivery;
- a complete candidate produces exactly one idempotent Branch-validation request;
- a changed candidate SHA cannot reuse prior database validation evidence.
