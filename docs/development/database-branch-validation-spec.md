# Database Branch Validation Specification

Status: control plane implemented; live Polar integration blocked by external access

## Current Environment Constraint

As of 2026-08-31, this project does not have authorized access to an enterprise PolarDB
production environment, a Polar Agentic Database test account, or confirmed Branch API
credentials. Production data must not be copied, queried, or used for testing without explicit
enterprise authorization.

Therefore the current implementation claim is deliberately limited:

- implemented: provider-neutral lifecycle, idempotency, evidence, cleanup recovery, persistence,
  and delivery-evidence binding;
- locally verifiable: orchestration behavior and PostgreSQL-compatible persistence contracts;
- not yet verified: live Polar Branch provisioning, historical production-like data validation,
  PolarDB engine compatibility, performance, capacity, and production deployment behavior.

RepoMesh must report the production provider as unavailable in this environment. Local
PostgreSQL or an in-memory provider may validate the control-plane contract, but their results
must not be presented as PolarDB production acceptance.

## Problem

RepoMesh can validate code commands, but a database change is currently tested only in the
Runner workspace. It cannot prove that a schema migration, data backfill, or changed query works
against representative existing data without touching the source database.

## Goal

For every database-changing candidate, RepoMesh can create an isolated database branch, run the
ordered database change stages, preserve bounded evidence, and delete the branch. The control
plane is provider-neutral; Polar Agentic Database is an infrastructure adapter behind the port.

## Lifecycle

```text
requested -> provisioning -> ready -> validating -> passed|failed -> cleaning -> cleaned
                                      \-> failed --------------------/
```

- One `(organization_id, idempotency_key)` identifies one logical run.
- Reusing the key with a different request is rejected.
- A passed result requires every migration, backfill, and verification command to exit zero.
- Cleanup is attempted after both success and failure. Cleanup failure is durable and retryable.
- A cleaned run retains evidence but never a live endpoint or credential.

## Ordered Stages

1. `migration`: apply schema changes in declared order.
2. `backfill`: transform existing data; this stage may be empty.
3. `verification`: check constraints, compatibility, and business query semantics.

The provider receives structured command declarations, not shell text assembled by RepoMesh.
Commands and their results are persisted as evidence. Output is bounded and sanitized by the
provider adapter. Passwords, connection strings, and raw environment variables are forbidden.

## Ownership

`review_validation` owns the run, status, evidence, and provider port. It does not own production
database credentials, cloud SDK configuration, application migrations, or candidate source code.

## Failure Rules

- Provisioning failure records `failed`; no validation command runs.
- The first non-zero command stops later stages and records `failed`.
- A provider exception records a stable error code, not exception internals.
- Cleanup failure leaves `cleanup_pending=true`; retry cleanup is idempotent.
- A run is valid delivery evidence only when validation passed and cleanup completed.

## Acceptance

- duplicate starts execute the provider once and return the same run;
- conflicting idempotency reuse is rejected;
- commands run migration -> backfill -> verification and stop on first failure;
- success and failure both request cleanup;
- cleanup can be retried without repeating validation;
- PostgreSQL persistence round-trips the full non-secret evidence;
- no database URL or provider credential appears in the stored payload.

## Deferred Provider Work

The first increment ships the control plane, contract tests, and a deterministic in-memory
provider. A live Polar adapter requires the competition/account endpoint, authentication method,
and API availability to be configured. Until then the product must report the provider as
unavailable rather than simulate a Polar success.

## Unblock Conditions

Live Polar work can resume only after all of the following are available:

1. an authorized non-production Polar Agentic Database or equivalent test tenant;
2. documented Branch API endpoint, authentication, quota, and cleanup policy;
3. a sanitized or synthetic dataset approved for migration testing;
4. an authorized PolarDB test target for compatibility verification;
5. an agreed rule for retaining logs and evidence without exposing customer data.

Direct access to an enterprise production database is not a prerequisite for development and is
not requested. The intended path is an authorized isolated test environment with sanitized data.
