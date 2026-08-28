---
name: project-intake
description: Convert an incoming requirement into a governed RepoMesh project intake. Use when a user, ticket, alert, incident, log, bill, security event or product request must become a scoped RepoMesh project before repository planning begins.
---

# Project Intake

Turn an incoming request into a project draft that can be planned and governed. Keep the output at
project level; repository implementation belongs to later Skills.

## Inputs

- Original request, ticket, alert, incident or business prompt.
- Available repository summaries and organization context.
- Constraints: deadline, risk tolerance, supervision mode, compliance and rollback needs.
- Known stakeholders or human approvers.

## Outputs

- Project goal, non-goals, acceptance criteria and success evidence.
- Initial risks, assumptions and questions requiring confirmation.
- Candidate repository areas for cross-repository planning.
- Idempotency key derived from source system id or stable request fingerprint.

## Workflow

1. Preserve the original request and source metadata.
2. Extract user value, constraints, acceptance criteria and non-goals.
3. Identify ambiguous scope and ask for confirmation before planning implementation.
4. Record risks that affect approval, rollback, credentials or external systems.
5. Create only the project intake draft.

## Dependencies

- RepoMesh project records and repository intelligence summaries.
- Optional ticket, alert, log or billing MCP sources through approved adapters.
- Human supervision configuration.

## Failure Handling

- If the source request is incomplete, create a draft with explicit questions.
- If repository ownership is unknown, pass the uncertainty to cross-repository planning.
- If the request demands unsafe action, block and require human confirmation.

## Safety

- Do not write repository code, specs or tasks.
- Do not infer secret values or privileged access from the request.
- Do not collapse multiple source requests into one project without an idempotency policy.

## Validation

- Acceptance criteria are observable and testable.
- Non-goals prevent accidental scope expansion.
- Every unresolved question is assigned to a decision owner.

## AgentTeams Mapping

The Organization Leader uses this Skill at project start. The resulting intake drives Repository
Leader consultation and later AgentTeams Team projection.

