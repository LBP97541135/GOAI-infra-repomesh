---
name: repository-spec-authoring
description: Author a repository-scoped engineering specification from project scope. Use when a Repository Leader must translate confirmed project scope and cross-repository contracts into a repository-owned Engineering Spec with paths, constraints, tests and acceptance gates.
---

# Repository Spec Authoring

Author repository-scoped specs after project scope is confirmed. The spec becomes the contract that
Worker tasks must execute against.

## Inputs

- Confirmed project scope and repository assignment.
- Repository intelligence summary, module ownership, README/module metadata and constraints.
- Cross-repository contracts, producer/consumer responsibilities and acceptance gates.
- Delivery, security and rollback policy.

## Outputs

- Engineering Spec with goals, non-goals, affected areas, allowed paths and constraints.
- Contract changes or confirmations before implementation details are shared.
- Acceptance criteria and required test commands.
- Risks, open questions and escalation triggers.

## Workflow

1. Read the repository/module documentation before defining implementation boundaries.
2. Map project acceptance criteria to repository-owned behavior.
3. Define or update public contracts before describing shared implementation.
4. Name allowed paths, forbidden paths and required tests.
5. Submit the spec for approval before task decomposition.

## Dependencies

- Repository intelligence module output.
- Specification and change-control records.
- Module `README.md`, `module.toml` and architecture docs.

## Failure Handling

- Escalate if project goals conflict with repository ownership or contracts.
- Block if required module documentation is absent or stale.
- Request cross-repository planning updates when dependency order changes.

## Safety

- Do not change project goals from a repository spec.
- Do not authorize writes outside the repository or module boundary.
- Do not approve implementation that depends on another module's concrete internals.

## Validation

- Every taskable requirement maps to a repository-owned acceptance criterion.
- Required tests can be run by Workers without extra hidden context.
- Cross-module imports target only producer contracts.

## AgentTeams Mapping

Repository Leaders use this Skill inside their AgentTeams Team. The Organization Leader consumes
submitted specs for project-level approval and dependency coordination.

