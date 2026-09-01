---
name: integration-run
description: Use when executing a cross-repository integration-test assignment - building the pinned combination, bringing the isolated environment up and down, running scenarios, and collecting the minimal evidence set. Never use to modify a business repository's code or to declare a release verdict.
---

# Integration Run Execution

This Skill belongs to Workers of a cross-repo test team. It is execution, not
judgement: the combination, the scenarios and the evidence standard are decided by
the team leader (`cross-repo-test`); this Skill's discipline is how they are carried
out.

## Inputs

- The assigned integration task: combination list (repository → pinned commit),
  scenario definitions and touch-point list.
- Read-only checkouts of the affected business repositories and the test-asset
  repository's scenario library.
- Environment definitions from the test-asset repository: ports, env vars, compose or
  dev entry points. Nothing is invented on the spot.

## Outputs

- A running isolated environment for the pinned combination, torn down afterwards.
- Per-scenario results: PASS / FAIL (with request-id and log slices) / INCONCLUSIVE
  (one rerun allowed, recorded).
- The minimal evidence set for every action: command, exit code, output excerpt,
  request-id.

## Workflow

1. Build the combination in one-time worktrees: each repository at its pinned commit,
   with cross-repo dependencies locally rewritten toward the candidate commit
   (Go `replace`, npm `file:`, pip editable — per ecosystem).
2. Run the contract gate first when the touch-point list names contracts; report
   incompatibilities before provisioning anything.
3. Bring the environment up from the catalog's test commands and the repository's
   own dev/compose entry points, pinned to the scenario's ports and env.
4. Execute scenarios: inject a request-id at the entry point, require every
   repository to propagate it, and collect structured logs per repository.
5. Tear the environment down; discard the one-time worktrees.

## Failure Handling

- Environment will not start → capture startup logs per repository and report; the
   leader attributes from the dependency graph plus those logs.
- Scenario is flaky → mark INCONCLUSIVE, rerun once, record both runs.
- Dependency rewrite is impossible for an ecosystem → report a blocker naming the
   repository and ecosystem; do not approximate with an unpinned dependency.

## Safety

- Rewrites live only in the one-time worktree and never flow back to any trunk.
- Business repositories are read-only: no commits, no pushes, no branch changes.
- No release verdicts and no attributions — those belong to the team leader; report
   raw evidence instead.

## Validation

- Every executed action has its command and exit code in the evidence.
- Every FAIL carries a request-id whose trace crosses the failing repository.
- Worktrees and environments from the round are gone when it ends.

## AgentTeams Mapping

Workers receive assignments in the test team's AgentTeams room and start governed
runs through the approved RepoMesh entry; results and evidence flow back through the
task report, not through free-form chat.
