# Context Management

## Responsibility

The Context module is RepoMesh's versioned knowledge and run-input boundary. It owns context
metadata, immutable versions, visibility scopes, effective permission evaluation, immutable run
bundles, ordered deltas, workspace plans, and access audit records.

It does not own source worktrees, secret values, chat history, vendor sessions, or object-storage
transport. PostgreSQL is the metadata source of truth; document bodies remain in S3/MinIO and are
addressed by URI and SHA-256. Matrix messages become context only after a RepoMesh command records
them as a versioned object.

## Four Input Layers

Each run composes references from four layers without copying all earlier content into every
prompt:

1. Bootstrap Context: agent identity, role, manager, durable responsibility and policy.
2. Repository Baseline: repository identity, responsibility paths, build and test entry points.
3. Project Membership Context: specification, confirmed scope, contracts, decisions, workstream.
4. TaskRun Context: task version, dependencies, base SHA, workspace and delegated permissions.

## Immutable Flow

```text
ContextObject -> ContextObjectVersion -> ContextBundle -> Context Workspace Plan
                                              |
                                              +-> ordered ContextDelta
                                              +-> ContextAccessEvent
```

- A logical object may receive new versions; an existing version is never overwritten.
- A bundle fixes exact version ids, content hashes, paths, permissions and expiry for one run.
- A supplemental delta is append-only and strictly sequenced.
- A delta that changes goal, acceptance, contract, base SHA, repository scope, or permission is
  rejected and must become a ChangeRequest and a new run input.
- Every allowed, denied, missing, or hash-mismatched read is an auditable event.

## Visibility And Permissions

Scopes are `organization`, `project_shared`, `team_private`, `task_private`, `run_private`, and
`secret`. Actions are independently granted as `discover`, `read`, `mount`, `publish`, `approve`,
and `export`.

Effective access uses this intersection:

```text
AgentPolicy & ProjectMembership & TaskSpec & RunDelegation - ExplicitDeny
```

The decision can constrain context objects, repositories, paths, tools, command categories,
network targets, secret purposes, expiry, and usage count. A denial at any layer rejects access.

Role visibility is only a ceiling. Workers may include `project_shared` in that ceiling so a Run
can mount an explicitly selected Spec or Contract, but the Project Membership layer contains the
exact allowed object IDs. It does not grant discovery or reading of every project-shared object.
Context publishing and approval use the Identity and Access action policy and object-type matrix;
read access never grants write access.

## Runtime Boundary

The Context module produces a `ContextWorkspacePlan` containing exact object versions, content
URIs, hashes, relative paths, and required-read markers. A Runtime-owned adapter implements
`ContextWorkspaceMaterializer` and must expose the resulting `context/` tree read-only. The code
worktree under `repo/` remains a separate Workspace integration.

## Persistence

Migration `20260802_0002` creates module-owned tables for objects, versions, relations, bundles,
bundle items, deltas, delta items, and access events. Context commands write StateEvent,
AuditEvent, and Outbox records in the same transaction as the owning context record.

Document bodies, large evidence, patches, and logs are not stored in these tables.

## Current Boundary

Implemented:

- provider-neutral public contracts;
- immutable domain objects and hash validation;
- permission intersection and explicit deny;
- object/version, bundle, delta, and access services;
- in-memory and PostgreSQL stores;
- workspace materialization port;
- transactional event, audit, and outbox writes.

Still required:

- S3/MinIO content adapter and upload lifecycle;
- physical read-only Context Workspace materializer;
- Identity/Project/Task permission-layer providers;
- publish/approve commands and Project Space query API;
- runtime hook that emits access observations;
- Secret Gateway, which must never materialize secret values as context files.
