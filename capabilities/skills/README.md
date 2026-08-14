# Skill Engineering Lifecycle

RepoMesh Skills are reviewed runtime capabilities, not prompt snippets. A Skill becomes available
to an Agent only after RepoMesh records its contract, ownership, quality evidence and AgentTeams
projection policy.

Use `evaluation-template.md` when releasing or materially changing a Skill.

## Skill Contract

Every Skill in this directory must define these sections:

| Section | Required content |
| --- | --- |
| Front matter | `name` and a description that states the exact trigger condition. |
| Inputs | Durable objects, ids, context bundles, commands or evidence the Skill may read. |
| Outputs | State transitions, evidence, decisions, artifacts or blocker reports the Skill may write. |
| Workflow | Ordered behavior; external side effects must name idempotency or retry handling. |
| Dependencies | RepoMesh modules, MCP servers, AgentTeams resources, tools and test commands. |
| Failure Handling | Retry, blocker, reassignment, escalation or rollback path. |
| Safety | Actions the Skill must not perform, especially scope, credentials and delivery boundaries. |
| Validation | Evidence a reviewer can inspect to decide whether the Skill worked. |
| AgentTeams Mapping | Manager, Repository Leader or Worker projection and message boundary. |

## Versioning

Skill versions use `MAJOR.MINOR.PATCH` semantics in the central registry, even when the source file
name is stable.

| Change | Version impact |
| --- | --- |
| Clarifies wording without changing inputs, outputs or authority | PATCH |
| Adds optional evidence, validation or failure handling | MINOR |
| Changes trigger condition, authority, required input, output shape or safety boundary | MAJOR |

Agents may run only Skills from an approved Skill Set snapshot. A running task keeps the snapshot it
started with; new Skill versions apply only to later assignments unless a human-approved retry
creates a new task attempt.

## Publication And Rollback

1. Author or update the Skill contract.
2. Run static review against the checklist above.
3. Add or update one scenario test, simulation, live evidence record or documented replay that
   proves the behavior.
4. Register the Skill version in the AgentTeams projection policy for eligible roles.
5. Roll out to a canary organization or repository team.
6. Promote after review evidence is accepted.

Rollback removes the new version from future Skill Set snapshots. Existing task attempts continue
with their immutable snapshot; unsafe in-flight attempts are stopped through task orchestration and
restarted with the previous approved Skill Set.

## Quality Evaluation

Each Skill release should keep a compact evaluation record:

| Field | Meaning |
| --- | --- |
| Scenario | The task or incident the Skill is expected to handle. |
| Agent role | Organization Leader, Repository Leader or Worker. |
| Preconditions | Required context, policy, MCP, credentials and repository state. |
| Expected decision | The state transition, evidence or blocker expected from the Skill. |
| Negative case | At least one boundary the Skill must refuse. |
| Evidence | Test command, trace id, live report, artifact or review note. |

Minimum release gate:

- Role and context visibility match `docs/architecture/agent-identity-catalog.md`.
- No Skill grants credentials directly to an Agent.
- Any MCP or SCM side effect has an idempotency key or documented retry policy.
- A mock coding adapter path remains usable so orchestration can be tested without vendor agents.
- `ruff check .` and `pytest` are run before pull request review, or an environment blocker is
  recorded.

## Reuse Model

Skills are reusable only through contracts. A Worker Skill can be reused across repositories when
the Runner policy supplies a different context bundle, path allowlist and test command set. A
Repository Leader Skill can be reused across projects when the repository Engineering Spec and
task DAG contract stay stable. Organization Leader Skills are reusable across industries when the
project intake, approval and delivery governance fields stay observable and auditable.
