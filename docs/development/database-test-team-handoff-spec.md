# Database Test Team Handoff Specification

Status: accepted for implementation

## Purpose

Repository Manager decides whether a Worker Task changes a database. The cross-repository Test
Team owns the independent test plan and evidence for that declared change. RepoMesh remains the
authority for Branch lifecycle, evidence binding, and Merge Gate decisions.

## Fixed Flow

```text
Manager Task databaseChange=true
-> business Worker supplies code/Migration/backfill
-> RepoMesh freezes candidate SHA
-> create one Test Team handoff plan (idempotent by Task + SHA)
-> Test Team Leader selects scenarios from the Manager required checks
-> Test Team Worker runs the approved plan in the test-assets repository
-> evidence/<run-id>/ is committed by platform delivery
-> RepoMesh verifies evidence and starts the database Branch validation
-> Test Team Leader reviews the result
-> Merge Gate accepts only matching SHA + passed/cleaned Branch evidence
```

## Authority And Permissions

- Manager owns `databaseChange` declaration and affected-table scope.
- Business Worker owns implementation artifacts and may report structured evidence.
- Test Team Leader owns test-plan decomposition and review, but cannot change Manager scope.
- Test Team Worker owns test recipes and evidence files in the test-assets repository.
- RepoMesh owns handoff idempotency, Branch execution, evidence integrity, and delivery gate.

## Handoff Plan

The plan contains only non-secret references:

- organization/project/repository/task ids;
- candidate SHA;
- test-team repository id and Team identity;
- Manager required checks and affected tables;
- required evidence path prefix;
- idempotency key and status.

It never contains database URLs, passwords, tokens, or production data.

## Failure Semantics

- Missing Manager declaration: `manager_review`.
- Missing Worker database evidence: `worker_rework`.
- Test Team plan missing a required check: `test_team_rework`.
- Test evidence for a different SHA: reject and create a new handoff plan.
- Test Team failure does not become a business Task failure; it blocks delivery and opens a
  rework/review decision.
- A Branch provider outage is `blocked_external`, not a passing test result.

## Compatibility

Tasks without `databaseChange` remain legacy and are not silently assigned to the Test Team. A
future v2 Manager contract may require explicit `declared=true` for every new Task.

## Acceptance

- one Task + candidate SHA creates one handoff plan;
- changing SHA creates a new plan;
- required checks and affected tables are copied immutably;
- Test Team cannot widen scope or inject credentials;
- missing checks produce a named rework decision;
- evidence is accepted only for the planned Task and SHA;
- handoff plan is linked to the existing Branch validation idempotency key.
