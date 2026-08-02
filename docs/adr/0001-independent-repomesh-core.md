# ADR 0001: Keep RepoMesh independent from AgentTeams

- Status: Superseded in part by ADR 0002
- Date: 2026-08-02

ADR 0002 replaces the external-dependency decision with first-party source and release ownership.
This ADR still governs RepoMesh business-state independence and adapter-based runtime boundaries.

## Context

RepoMesh needs AgentTeams capabilities such as manager/worker lifecycle, team topology, skills,
and message transport. RepoMesh also owns long-lived enterprise delivery state that AgentTeams
does not: projects, repository profiles, specifications, tasks, context snapshots, validation
evidence, change sets, and rollback history.

Forking AgentTeams would couple RepoMesh releases and domain state to an upstream runtime and
make upgrades expensive. Reimplementing the runtime would duplicate non-differentiating work.

## Decision

RepoMesh is a standalone modular monolith. It integrates with a pinned AgentTeams release via
a narrow adapter. AgentTeams resources are runtime projections, never RepoMesh's fact source.

The first coding-agent integration is a deterministic mock behind the same port that future
Codex, Claude Code, Cursor, or internal agents will implement.

## Consequences

- AgentTeams can be upgraded or replaced behind contract tests.
- RepoMesh can run tests without Kubernetes, Matrix, or a vendor coding agent.
- Integration work must explicitly map identifiers and reconcile runtime events.
- A thin upstream fork remains possible only for a proven missing extension point.

