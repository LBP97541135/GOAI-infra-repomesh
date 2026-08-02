# ADR 0002: Adopt AgentTeams As A First-Party Runtime Component

- Status: Accepted
- Date: 2026-08-02
- Supersedes: ADR 0001 only for AgentTeams source and release ownership

## Context

RepoMesh needs a product control plane for enterprise delivery state and a runtime control plane
for agent processes. AgentTeams already provides a Go controller, declarative Manager/Worker/Team
resources, Matrix collaboration, object storage integration, gateway configuration, and container
lifecycle. RepoMesh already provides the Python boundaries for projects, specifications, tasks,
context, validation, delivery, and coding-agent adapters.

Treating AgentTeams only as an external dependency prevents RepoMesh from evolving missing runtime
capabilities such as run correlation, lifecycle events, visibility enforcement, skill policy, and a
Python coding runner. Merging both systems into one process would instead couple unrelated domain
state, languages, storage models, and failure modes.

## Decision

RepoMesh is one product with three separately testable planes:

1. The Python RepoMesh product control plane owns durable enterprise delivery state.
2. The Go AgentTeams runtime control plane owns desired runtime resources and their reconciliation.
3. The Python RepoMesh Runner executes coding tasks inside an AgentTeams-managed Worker.

AgentTeams is a first-party source, build, release, and deployment component under
`components/agentteams`. It may be changed when a versioned runtime contract requires behavior not
available through an existing extension point. It remains a separate process and is not a source
of truth for RepoMesh business aggregates.

The language-neutral contract under `contracts/runtime/v1` is authoritative at process boundaries.
Python modules and Go packages may generate or implement types from it, but neither side may import
the other component's internal implementation.

## State Ownership

| State | Owner |
| --- | --- |
| Project, specification, task, context, run, validation, delivery | RepoMesh PostgreSQL |
| Manager, Worker, Team, Human desired/runtime state | AgentTeams Controller |
| Coding process and native agent session | RepoMesh Runner inside a Worker |
| Collaboration messages | Matrix, ingested into RepoMesh only as explicit observations |
| Large immutable bodies and runtime artifacts | S3/MinIO, referenced by URI and content hash |

AgentTeams resources are runtime projections. RepoMesh persists stable resource bindings and can
reconcile them after a Controller restart or replacement. Neither component reads or writes the
other component's database.

## Source And Upstream Policy

Before its first local Controller patch, RepoMesh will create a product fork of AgentTeams and
import reviewed product-fork revisions with Git subtree. The official AgentTeams repository
remains an upstream source. Runtime contract
changes are reviewed before Go or Python implementation changes, and the exact fork and upstream
commits are recorded for every release.

Local AgentTeams changes require Controller tests, RepoMesh adapter contract tests, and a live
compatibility test. Changes that are generally useful should be contributed upstream when possible.

## Consequences

- Users receive one product, installation, API entry, UI, and release version.
- RepoMesh can add runtime behavior without waiting for an external release.
- Go remains responsible for resource reconciliation; Python remains responsible for coding work.
- Unit tests can still run without Docker, Matrix, AgentTeams, or a vendor coding agent.
- Cross-plane changes require versioned contracts, compatibility tests, and explicit migration.
- A unified release must report the RepoMesh, AgentTeams fork, upstream, and Runtime API versions.
