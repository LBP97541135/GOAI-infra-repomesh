---
name: blocker-reporting
description: Report task blockers through the repository leadership chain. Use when a Worker or Repository Leader cannot continue because requirements, contracts, permissions, tools, tests, dependencies, or runtime state are missing, conflicting, unsafe, or outside the approved scope.
---

# Blocker Reporting

Report blockers as structured evidence, not as free-form status. Keep the message inside the
authorized AgentTeams path: Worker to Repository Leader, then Repository Leader to Organization
Leader when project scope, contracts or delivery gates are affected.

## Inputs

- Current project, repository, task and run identifiers.
- Approved Spec or Task Spec section that is blocked.
- Evidence: failing command, log excerpt, trace id, missing context object, permission denial,
  dependency status, or conflicting contract.
- Impact: blocked acceptance criteria, affected repositories, delivery risk and urgency.
- Proposed options with expected owner and verification step.

## Outputs

- `BLOCKED` report containing facts, evidence, impact, options and requested decision.
- Optional retry recommendation when the failure is transient and idempotent.
- Escalation request when a contract, scope, security, approval or rollback decision is needed.

## Workflow

1. State the exact blocked action and why continuing would be unsafe or invalid.
2. Attach reproducible evidence and identifiers before suggesting a fix.
3. Distinguish transient tool/runtime failures from scope or contract conflicts.
4. Send the report only to the next authorized leader.
5. Wait for a new approved decision before changing scope, permissions or acceptance criteria.

## Dependencies

- RepoMesh task, context, collaboration and observability records.
- AgentTeams Team Room or Leader coordination room.
- Related MCP/tool execution evidence when the blocker came from an external side effect.

## Failure Handling

- If evidence is incomplete, report the missing evidence as part of the blocker.
- If Matrix delivery fails, persist the blocker in RepoMesh collaboration state and retry with the
  same idempotency key.
- If the Repository Leader is unavailable, escalate through the configured project supervision path.

## Safety

- Do not edit the Spec, Task Spec, contracts or repository code while reporting the blocker.
- Do not contact peer Workers or another repository directly.
- Do not include secret values; reference credential ids or redacted error classes only.

## Validation

- The blocked artifact is identified by stable id.
- Evidence is sufficient for the receiver to reproduce or decide.
- The report names the next required decision and the action that must remain paused.

## AgentTeams Mapping

Workers use this Skill to send governed blocker messages to their Repository Leader. Repository
Leaders reuse it when forwarding cross-repository or project-scope blockers to the Organization
Leader.

