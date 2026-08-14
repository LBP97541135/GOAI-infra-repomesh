---
name: test-review
description: Evaluate whether test evidence covers task acceptance criteria and risks. Use when a Repository Leader, reviewer, or validation gate must decide whether Worker test results are sufficient for task acceptance, retry, or escalation.
---

# Test Review

Review evidence quality, not just exit codes. The goal is to decide whether the task behavior and
risk areas are actually covered.

## Inputs

- Task acceptance criteria, changed files and risk notes.
- Test commands, logs, artifacts, CI observations and validation snapshot ids.
- Candidate commit SHA and any skipped or failed test records.

## Outputs

- Coverage decision: `SUFFICIENT`, `NEEDS_RETEST`, `INSUFFICIENT` or `ESCALATE`.
- Acceptance-to-test mapping.
- Required additional commands or evidence gaps.

## Workflow

1. List each acceptance criterion and the evidence intended to cover it.
2. Check command scope, assertions, fixtures and changed-file relevance.
3. Identify missing negative cases, integration risk and stale evidence.
4. Distinguish flaky/environment failures from product failures.
5. Record the test review decision for repository review.

## Dependencies

- Review-validation snapshots and Runner/CI logs.
- Approved Task Spec and Engineering Spec.
- Observability traces for tool and model execution where relevant.

## Failure Handling

- Request retest when evidence is missing, stale or not tied to the candidate SHA.
- Escalate when required tests cannot run in the current environment.
- Do not accept a result by substituting manual claims for missing evidence.

## Safety

- Do not alter the candidate code or tests during review.
- Do not hide failed, skipped or flaky commands.
- Do not require tests outside the approved scope without change-control approval.

## Validation

- Every acceptance criterion has direct evidence or a documented risk-based exception.
- Every required command has exit code, cwd and log reference.
- The decision is reproducible from stored evidence.

## AgentTeams Mapping

Repository Leaders use this Skill before accepting Worker results. Organization Leaders consume
the resulting validation snapshots during ChangeSet governance.

