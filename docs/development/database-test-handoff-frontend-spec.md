# Database Test Handoff Frontend Specification

Status: accepted for implementation

## Goal

Show the database-test handoff as a first-class, task-scoped status block in the Issue detail
page. The browser reads server state and never infers database impact from titles or free text.

## States

The panel renders `planned`, `testing`, `test_team_rework`, `evidence_ready`, Branch validation
states, and `blocked_external` distinctly. It polls only while work is active and never overlaps
requests. Issue navigation cancels the previous poll.

## Safety

The panel exposes no database credential, Manager-edit operation, Branch mutation, or test-team
Leader token. Approval remains an authenticated backend member operation.

## Acceptance

- a database Task shows its Handoff status beside execution evidence;
- non-database Tasks render no panel;
- active states poll and terminal states stop polling;
- API errors are local to the panel and have retry action;
- Candidate SHA, required checks, affected tables, evidence path, and Branch status are shown;
- TypeScript build and lint pass.
