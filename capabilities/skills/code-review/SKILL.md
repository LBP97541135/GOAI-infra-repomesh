---
name: code-review
description: Review a worker diff against its approved task and specification. Use when a Repository Leader or reviewer must decide whether a Worker result should be accepted, retried, reassigned, or escalated based on code, tests, scope, contracts and evidence.
---

# Code Review

Review a Worker result read-only. Judge the diff against the approved Task Spec and repository
Engineering Spec, not against new preferences discovered during review.

## Inputs

- Worker task id, run id, candidate commit SHA and changed-file list.
- Approved Task Spec, repository Engineering Spec and relevant contracts.
- Test evidence, Runner events, path-policy result and context bundle hash.
- Repository diff and any linked blocker or retry history.

## Outputs

- Review decision: `ACCEPT`, `RETRY`, `REASSIGN` or `ESCALATE`.
- Findings with severity, file/line references where available, and evidence.
- Required retest commands or contract decisions for non-accept outcomes.

## Workflow

1. Confirm the candidate commit matches the reported run and approved context bundle.
2. Check every changed file is inside allowed paths and supports the task acceptance criteria.
3. Review correctness, compatibility, error handling, idempotency and maintainability.
4. Map tests to acceptance criteria and risk areas.
5. Return a decision with concrete next action.

## Dependencies

- RepoMesh task/result stores and validation snapshots.
- Git diff or SCM observation for the candidate commit.
- Test logs and Runner path/tool enforcement evidence.

## Failure Handling

- Use `RETRY` for fixable implementation or test gaps inside the same scope.
- Use `REASSIGN` when the Worker cannot complete with its current capability or environment.
- Use `ESCALATE` when the approved task or contract is wrong, incomplete or unsafe.

## Safety

- Do not modify code, create commits, push branches or merge PRs during review.
- Do not expand acceptance criteria after execution; request a change decision instead.
- Do not treat AgentTeams chat text as the source of truth for acceptance.

## Validation

- Each finding ties to a changed behavior, contract, test gap or policy violation.
- Accepted results include passing evidence for every required command.
- Rejected results include a retry or escalation path that can be executed.

## AgentTeams Mapping

Repository Leaders use this Skill after Workers publish results. The Organization Leader consumes
accepted repository review evidence during project-level validation and delivery governance.

