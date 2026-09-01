---
name: cross-repo-test
description: Use when leading a cross-repository integration-test team - judging the blast radius of a declared candidate, pinning the combination under test, attributing failures to exactly one repository with evidence, and writing the integration report. Never use to authorize a release or to modify a business repository.
---

# Cross-Repo Test Leadership

This Skill belongs to the leader of a cross-repo test team (a repository team whose
governance repository is the test-asset repository). The team produces evidence;
the Organization Leader decides. This Skill covers what machines cannot judge:
influence-scope trade-offs, attribution discipline, and report wording.

## Inputs

- Candidate declarations from Repository Leaders (repository, pinned commit, declared
  touch points), each backed by an in-repository green run.
- The organization dependency graph from `repository_intelligence` (dependency
  manifests, contract references, service calls).
- The cross-repo scenario library and combination history in the test-asset repository.
- Worker evidence from `integration-run` runs: commands, exit codes, output slices,
  request-id traces.

## Outputs

- A combination list for each round: one pinned commit per repository.
- Attribution decisions: at most one owning repository per failure, each with its
  request-id chain, or an explicit INCONCLUSIVE escalation.
- The integration report (idempotency key = combination hash): scenario results,
  contract-change notes, attribution evidence, per-repository verdicts.
- Return tasks toward the owning repository's Repository Leader — never direct code
  changes.

## Workflow

1. Compute the affected repository set and touch-point list from the dependency graph.
   When the candidate's declared touch points disagree with the graph, the graph wins
   and the disagreement is recorded in the report — never silently resolved.
2. Pin the combination: candidate repository at the candidate commit, every other
   affected repository at its latest declared candidate or trunk. The combination
   list is part of the round's idempotency key.
3. Dispatch scenario work to test Workers with `integration-run`; contract gates run
   before environments are provisioned.
4. For each failure, attribute it using the dependency graph plus the request-id
   chain: exactly one repository, or INCONCLUSIVE with all evidence attached.
5. Write the report and file return tasks for attributed failures.

## Failure Handling

- Contract incompatibility → FAIL immediately, attributed to the candidate repository;
  skip environment provisioning for that combination.
- Attribution cannot be established → INCONCLUSIVE, escalated to the Organization
  Leader with the full evidence chain; do not guess an owner.
- Environment will not start → treat as a failure with attribution from startup logs
  and the dependency graph.
- Cross-repo touch points hit but zero scenarios exist to run → record
  SCENARIO_MISSING explicitly; a silent PASS is forbidden.

## Safety

- The team writes only to the test-asset repository. Business repositories are
   read-only: checkout, build and run are allowed; code changes are not.
- No release authority: verdicts are recommendations; the Organization Leader's
   delivery gate consumes the report and nothing else.
- Every external side effect (environment creation, report archival, return-task
   dispatch) carries the combination hash as its idempotency key.

## Validation

- Every attribution cites a request-id chain that traverses the failing step.
- Every PASS cites the scenario ids that ran; PASS without scenarios is
  SCENARIO_MISSING instead.
- The report's combination list matches the pinned commits actually checked out.

## AgentTeams Mapping

The team leader coordinates from the test team's AgentTeams room; return tasks flow
through the existing task orchestration to the owning repository's Leader room. The
Organization Leader consumes the report through the delivery gate, not through rooms.
