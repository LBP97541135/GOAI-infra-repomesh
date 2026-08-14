---
name: cross-repo-planning
description: Select repository scope and define cross-repository dependencies. Use when an Organization Leader converts project intake into repository participation, ownership, dependency order, contract responsibilities, and project-level acceptance boundaries.
---

# Cross-Repository Planning

Plan repository participation before implementation. RepoMesh project state is the source of truth;
AgentTeams rooms are the coordination surface.

## Inputs

- Project intake: goal, non-goals, acceptance criteria, risks and unresolved questions.
- Repository intelligence summaries, dependency graph and ownership hints.
- Repository Leader confirmations and known cross-repository contracts.
- Delivery policy, supervision mode and rollback expectations.

## Outputs

- Confirmed repository scope with in-scope and out-of-scope rationale.
- Cross-repository dependency order and contract owner per shared API/data/event boundary.
- Workstream assignments for Repository Leaders.
- Project-level acceptance and integration gates.

## Workflow

1. Match user value and acceptance criteria to repositories and contracts.
2. Ask Repository Leaders to verify ownership and feasibility for their repositories.
3. Define producer/consumer contracts before task implementation details.
4. Order repositories by dependency and rollback risk.
5. Persist the confirmed scope before Repository Leaders author specs.

## Dependencies

- Repository intelligence profiles and dependency evidence.
- Project, specification and change-control records.
- AgentTeams Organization Leader and Repository Leader communication paths.

## Failure Handling

- If repository evidence conflicts, mark the scope `BLOCKED` and request confirmation.
- If a contract owner is unclear, create a change-control decision instead of guessing.
- If a repository is unavailable, plan a degraded path or postpone affected tasks.

## Safety

- Do not dispatch Workers or assign coding tasks from this Skill.
- Do not grant a Worker cross-repository write permissions.
- Do not use Matrix messages as durable planning state.

## Validation

- Every in-scope repository maps to at least one acceptance criterion or dependency.
- Every cross-repository dependency has a producer, consumer and verification gate.
- Every excluded repository has evidence-backed rationale.

## AgentTeams Mapping

The Organization Leader uses this Skill to coordinate Repository Leaders. The result becomes the
project execution group that is later projected to one AgentTeams Team per repository.

