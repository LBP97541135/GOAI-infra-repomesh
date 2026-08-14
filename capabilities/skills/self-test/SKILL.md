---
name: self-test
description: Run task-scoped tests and publish structured evidence. Use when a Worker or Repository Leader must verify a task, candidate commit, retry, regression, or release gate with executable commands and durable evidence.
---

# Self Test

Run tests before claiming completion. Evidence must be reproducible and tied to the candidate commit
or validation snapshot.

## Inputs

- Task id, run id, candidate commit SHA and workspace path.
- Required commands from Task Spec and repository Engineering Spec.
- Changed files, risk areas and known flaky or environment-dependent tests.
- Tool permissions and sandbox constraints.

## Outputs

- Structured test evidence: command, cwd, environment summary, exit code, duration and log path.
- PASS/FAIL status per acceptance criterion.
- Blocker report when a required command cannot run.

## Workflow

1. Run the narrowest task-specific tests first.
2. Run required repository regression commands from the approved spec.
3. Capture stdout/stderr, artifacts, trace ids and command exit codes.
4. Map results to acceptance criteria.
5. Publish evidence before returning a Worker result.

## Dependencies

- Repository test tools and approved command allowlist.
- RepoMesh Runner events, validation snapshots and observability records.
- Optional CI integration for post-PR verification.

## Failure Handling

- Report failed tests honestly and keep the workspace for diagnosis.
- Distinguish product failures from missing dependencies or environment failures.
- Retry only commands with a documented retry policy or idempotent behavior.

## Safety

- Do not mark tests as passed based on code inspection alone.
- Do not skip required commands without recording the reason and owner approval.
- Do not leak secrets in logs or trace attributes.

## Validation

- Every required acceptance criterion has direct or risk-based test evidence.
- The evidence references the same commit SHA that review and delivery will use.
- Failed or skipped commands are visible to the next reviewer.

## AgentTeams Mapping

Workers use this Skill before reporting results. Repository Leaders use it to request focused
retests or verify repository integration before project delivery.

