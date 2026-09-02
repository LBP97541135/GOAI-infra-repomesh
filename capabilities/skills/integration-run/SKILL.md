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
- The round's evidence directory, committed and pushed to the test-asset
  repository as `evidence/<run-id>/` **before** the environment is torn down:
  combination list, per-scenario minimal evidence sets, the round's actual
  ports and namespace, and the round verdict.

## Workflow

The Worker does not build, run or tear down anything itself: Worker
containers have no Docker and no host access, so the round executes as a
**governed run on the platform's Runner execution plane** from the test-asset
repository's recipe (`environments/<env>/run_round.py`). The Worker's job is
the four steps below; the recipe's own discipline — TTL sweep first, every
resource under the `itest-<run-id>` prefix, ports injected never hard-coded,
the environment's concurrency cap, evidence written before teardown — is the
recipe's to keep and the Worker's to check in the receipt.

1. Check the assignment: it names a frozen combination file
   (`scenarios/<scenario>/combinations/<name>.json`) that the Manager froze
   into the acceptance basis. No combination named, or a request to "just
   use trunk", is not an assignment — report it back rather than inventing
   pins.
2. Start the governed run through the approved entry
   (`POST /agent-actions/start-worker-task` when you run as a Bridge member;
   automatic dispatch when your resource runs the Runner runtime). The task's
   `test_commands` invoke the recipe; you add nothing to it and touch no
   business repository.
3. Wait for the receipt. Read the exit code by the frozen convention
   (`0` all PASS, `1` any FAIL, `2` BLOCKED) and the artifact pointers to
   `evidence/<run-id>/` on the candidate branch the platform delivered.
   Confirm the round's `itest-<run-id>/` root is gone and that the evidence
   directory has all four sections before you treat the run as finished.
4. Report raw evidence upward: exit code, per-step results, request-ids of
   every FAIL, and the evidence pointers. No attribution, no verdict on the
   release — those belong to the team leader.

## Failure Handling

- Environment will not start → capture startup logs per repository and report; the
   leader attributes from the dependency graph plus those logs.
- Scenario is flaky → mark INCONCLUSIVE, rerun once, record both runs.
- Dependency rewrite is impossible for an ecosystem → report a blocker naming the
   repository and ecosystem; do not approximate with an unpinned dependency.
- Round is BLOCKED (exit 2: unbuildable combination, missing pin, dead
   prerequisite) → the recipe still writes `evidence/<run-id>/` with the
   blocking reason and whatever partial evidence exists, and the platform
   still delivers it; report BLOCKED with the pointer. A blocked round with no
   evidence directory is indistinguishable from a round that never ran —
   if the receipt has no pointer, say so explicitly.
- The run died mid-round (Runner or Worker) → the task ledger settles that
   round; the rerun takes a **new** run-id and nothing is back-filled into the
   dead round's directory. The next round's sweep reclaims its leftovers.
- The environment is compose-based → not executable in v1 on any plane;
   report SCENARIO_UNRUNNABLE with the environment name instead of
   improvising a substitute.

## Safety

- Rewrites live only in the one-time worktree and never flow back to any trunk.
- Business repositories are read-only: no commits, no pushes, no branch changes.
- No release verdicts and no attributions — those belong to the team leader; report
   raw evidence instead.

## Validation

- Every executed action has its command and exit code in the evidence.
- Every FAIL carries a request-id whose trace crosses the failing repository.
- Output excerpts are capped at 64KB each, with truncation noted where applied;
  raw full logs never enter the test-asset repository — the excerpt and its cap
  are the evidence, not an abridged apology for a log file.
- Worktrees and environments from the round are gone when it ends.

## AgentTeams Mapping

Workers receive assignments in the test team's AgentTeams room and start governed
runs through the approved RepoMesh entry; results and evidence flow back through the
task report, not through free-form chat.
