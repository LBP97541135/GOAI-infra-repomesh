# Skill Release Evaluation Template

Copy this template for every new or materially changed Skill version.

## Release

| Field | Value |
| --- | --- |
| Skill name |  |
| Version |  |
| Change type | PATCH / MINOR / MAJOR |
| Owner |  |
| Target roles | Organization Leader / Repository Leader / Worker |
| AgentTeams projection | Manager / Team Leader Worker / Worker |

## Scenario

| Field | Value |
| --- | --- |
| Scenario id |  |
| Trigger condition |  |
| Preconditions |  |
| Approved context bundle or fixture |  |
| Required MCP/tools |  |
| Required credentials | Credential references only; no values. |

## Expected Behavior

| Field | Value |
| --- | --- |
| Expected outputs |  |
| Expected state transition |  |
| Idempotency key or retry policy |  |
| Human approval gate |  |
| Rollback or compensation path |  |

## Negative Case

| Field | Value |
| --- | --- |
| Boundary being tested |  |
| Refused action |  |
| Expected blocker or escalation |  |
| Evidence |  |

## Evidence

| Check | Result |
| --- | --- |
| Static Skill contract checklist | PASS / FAIL |
| Mock adapter scenario | PASS / FAIL / N/A |
| Unit or contract test | Command and result |
| Live or replay evidence | Link to report, trace id or artifact |
| Security review | PASS / FAIL |
| `ruff check .` | PASS / FAIL / BLOCKED |
| `pytest` | PASS / FAIL / BLOCKED |

## Decision

| Field | Value |
| --- | --- |
| Release decision | APPROVE / HOLD / ROLLBACK |
| Decision owner |  |
| Follow-up tasks |  |

