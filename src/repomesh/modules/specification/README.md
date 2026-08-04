# Specification

Owns versioned Engineering, Contract, Repository, and Task specifications. PostgreSQL is the
source of truth; Markdown is a deterministic, read-only view generated for an agent run.

The lifecycle is `draft -> in_review -> approved|frozen`. Revisions create immutable versions
with source hashes instead of overwriting approved content.

`BuildCodingAgentPackage` is the boundary presented to a coding agent. It verifies that the
requesting worker is the actual task assignee, selects exactly one approved Task Spec, and emits
only the current instruction, acceptance criteria, constraints, required dependencies, interface
requirements, allowed paths, test commands, and one generated `current-task.md` file. It does not
include the PRD, complete Engineering Spec, other repository specifications, other tasks, chat
history, or secrets.

This module does not schedule tasks, create worktrees, invoke coding agents, push branches, or
merge pull requests.
