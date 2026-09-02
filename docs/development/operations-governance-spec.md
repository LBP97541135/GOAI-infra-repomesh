# Operations Governance Specification

Status: accepted for P0 implementation

## Problem

RepoMesh has leases, retries, alerts, traces, logs, and usage records, but they are independent
features. Operators cannot answer one question: is the platform within capacity, will an alert
reach anyone, what automatic protection was applied, how long evidence is retained, and whether
this deployment is ready to upgrade or recover?

## P0 Scope

`observability` owns operational policy, notification delivery evidence, automatic protection
evidence, and retention execution. It remains read-only toward business modules: automated
actions may only invoke explicit operational ports and can never mutate a Task or merge code by
writing another module's tables.

## Capacity And Backpressure

A policy defines ceilings for queued Runner dispatches, active Worker executions, pending SCM
commands, and pending bootstrap operations. A capacity snapshot reports current value, limit,
utilization, and state:

- `available`: below 80%;
- `pressured`: 80-99%, accept only already-idempotent/recovery work;
- `saturated`: at or above limit, reject new work with retry-after;
- `unknown`: source unavailable; production fails closed for new work.

The first increment provides the decision service and API contract. Each business intake point is
wired incrementally after its owning module supplies a count port; no cross-schema queries are
allowed.

## Alert Notification And Automatic Actions

Every new firing alert produces one idempotent notification operation. Notification adapters get
a redacted event, never prompts, tool arguments, tokens, or stack traces. Delivery records
attempts, status, safe error code, and timestamps.

Automatic actions are allow-listed and reversible:

- `none`: notify only;
- `degrade_writes`: refuse new optional/external writes through an operational gate;
- `pause_intake`: refuse new projects while running/recovery work continues.

No action may merge, rollback, delete business data, or stop a running Agent. Resolution clears
only the action owned by that alert; multiple firing alerts compose conservatively.

## Retention And Redaction

Policies independently retain usage, trace events, logs, resolved alerts, and notification
evidence. Cleanup runs in bounded batches and records counts. Active alerts, audit records, and
business evidence are never removed by this service. Existing recorders remain responsible for
redacting before write; retention is not a substitute for redaction.

## Unified Correlation

The query API accepts one of `issue_id`, `task_id`, `run_id`, `trace_id`, or `correlation_id` and
returns links/counts from each source that can prove that identity. Heuristic time-window matches
must be labelled approximate; missing identifiers stay missing rather than guessed.

## Upgrade And Disaster-Recovery Readiness

The control plane publishes checks, not false guarantees:

- one Alembic head and current revision known;
- backup command/target configured and last successful backup age;
- restore drill evidence age;
- AgentTeams state/object storage backup status;
- Runner workspaces classified as disposable or externally backed up;
- release and previous-release compatibility identifiers.

Unavailable backup infrastructure is `blocked_external`, not `passed`. P0 stores and exposes the
check results; deployment-specific backup/restore executors remain infrastructure adapters.

## Acceptance

- capacity decisions are deterministic at 80% and 100% boundaries;
- saturated or unknown production capacity fails closed with retry-after;
- one firing transition creates one notification/action operation despite reevaluation;
- unsafe automatic actions are impossible by enum and port contract;
- retention deletes only eligible resolved/telemetry rows in bounded batches;
- policies reject zero/negative limits and unsafe retention periods;
- readiness distinguishes passed, failed, unknown, and blocked-external;
- all persisted errors are stable codes and contain no secret-bearing exception text.
