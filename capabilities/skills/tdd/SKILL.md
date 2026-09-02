---
name: tdd
description: Use for every RepoMesh coding task. Apply the red-green-refactor cycle whenever a Worker writes production code - no production code may exist without a failing test that covers the behavior it implements.
---

# Test-Driven Development

The iron law: **no production code is written without a failing test that covers the
behavior that code implements.** If code was written first, delete it and start from the
test — "keep it for reference" is not allowed.

This Skill is transplanted from superpowers' `test-driven-development`, adapted to
RepoMesh's governed execution plane. The difference is stated once and matters
everywhere: superpowers relies on the agent's own discipline to obey the law; in
RepoMesh the law is backed by the execution plane (the Runner records every test run,
and path policy can deny production paths while no failing test exists). This Skill
owns what the machine cannot judge — test quality, minimal implementation, and
refactoring taste.

## Inputs

- The materialized Task Spec at `.repomesh/context/current-task.md`, including its
  acceptance criteria and Required tests section.
- The task's allowed paths, allowed tools and test commands from the Context Bundle.
- The mounted RepoMesh skills and repository-local test tooling.

## Outputs

- A test suite where every behavior the task delivers is covered by a test that once
  failed for the right reason, then passed.
- A candidate commit whose changed paths and test evidence are recorded by the Runner.
- Blocker reports when the cycle cannot proceed (unrunnable tests, contradictory
  acceptance criteria).

## Workflow

1. **RED**: write exactly one failing test for one behavior. Name the test after the
   behavior. Use real code paths; do not mock the thing under test.
2. Run the test and **watch it fail for the right reason** — an assertion failure, not
   a collection error or an import error.
3. **GREEN**: write the minimal code that makes that test pass. No opportunistic
   refactoring, no speculative features (YAGNI).
4. Run the full task test set and confirm it is green.
5. **REFACTOR** — only while green: remove duplication, rename, extract. Behavior
   stays; the suite stays green.
6. Repeat with the next failing test until the acceptance criteria are met.

## Failure Handling

- Test passes immediately → it tests existing behavior; fix the test, not the code.
- Test errors instead of failing → repair it until it fails on an assertion.
- Test goes red during GREEN → fix the code, never the test.
- A bug is discovered → write the reproducing test first, watch it fail, then fix.
- Tests cannot run at all → report a blocker with the command and output; do not
  proceed on unverified code.

## Safety

- Never weaken, skip, slow-down-mark, or delete a test to reach green. The platform
  records test runs and hands the evidence to the Repository Leader's `test-review`.
- Never write outside the task's allowed paths or touch other tasks' scope.
- Exemptions (throwaway prototypes, generated code, pure configuration) are not
  self-granted: they require a governance decision recorded on the task.

## Validation

- Every test in the suite has a recorded red state (command, output excerpt) from
  before its implementation existed.
- The final full-suite run is green and its command and exit code are in the evidence.
- Changed paths all fall inside the task's allowed paths.

## AgentTeams Mapping

The Worker follows this Skill inside the Runner workspace; the platform gates phases
between red and green and records the evidence. Repository Leaders use `test-review`
to judge whether the red evidence exists and failed for the right reason. The
AgentTeams team-room message remains only the dispatch signal.
