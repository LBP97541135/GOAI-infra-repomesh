---
name: worker-result-evaluation
description: Evaluate worker output and choose accept, retry, reassign, or escalate. Use when a Repository Leader receives Worker result evidence and must decide whether the task advances, needs another attempt, needs a different Worker, or requires higher-level change control.
---

# Worker Result Evaluation

Evaluate the whole Worker result: scope, diff, tests, artifacts and policy evidence. Only accepted
results may advance repository task progress.

## Inputs

- Worker task id, run id, candidate commit SHA, changed files and result summary.
- Approved Task Spec, acceptance criteria and allowed paths/tools.
- Test evidence, validation snapshot, Runner events and blocker/retry history.
- Repository Leader review notes.

## Outputs

- Decision: `ACCEPT`, `RETRY`, `REASSIGN` or `ESCALATE`.
- Required next action and owner.
- Updated task progress only for `ACCEPT`.

## Workflow

1. Confirm the result belongs to the assigned Worker and active task attempt.
2. Check commit, changed files and context hash against Runner evidence.
3. Review implementation and test sufficiency.
4. Decide whether the task is complete, retryable, reassignment-worthy or scope-blocked.
5. Persist the decision and notify the Worker or Organization Leader as appropriate.

## Dependencies

- Task orchestration, review-validation, Runner and collaboration records.
- Git/SCM diff evidence for the candidate commit.
- Blocker-reporting for escalation paths.

## Failure Handling

- `RETRY` for task-scoped defects or missing tests.
- `REASSIGN` for capability, environment or repeated execution failure.
- `ESCALATE` for contract, project-scope, approval or rollback decisions.

## Safety

- Do not mark progress complete without an accepted candidate and evidence.
- Do not edit the Worker workspace or candidate commit during evaluation.
- Do not approve changes outside the delegated path or task scope.

## Validation

- Accepted results satisfy every acceptance criterion with evidence.
- Retry and reassignment decisions include concrete missing work.
- Escalations identify the exact decision that the Repository Leader cannot make alone.

## AgentTeams Mapping

Repository Leaders use this Skill after receiving Worker completion messages. The outcome drives
RepoMesh task state and later Organization Leader delivery governance.

