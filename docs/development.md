# Development workflow

## Branches and pull requests

Use short-lived branches and require CI before merge. A practical ownership split for the first
milestone is API, repository intelligence, runtime integrations, and platform/persistence.
Avoid assigning ownership by horizontal layers when one feature can be delivered vertically.

Every pull request should state:

1. The observable behavior that changed.
2. The module contract affected.
3. How it was tested.
4. Any migration, rollback, or external compatibility concern.

## Near-term milestones

1. Implement GitHub repository ingestion and profile freshness tracking.
2. Add Project, EngineeringSpec, Task, ContextSnapshot, Validation, and ChangeSet modules.
3. Pin an AgentTeams release and build resource mapping plus contract tests.
4. Add one real coding-agent adapter and sandboxed execution.
5. Build audit timelines, checkpoints, retry policies, and rollback workflows.

The repository-discovery heuristic is a transparent baseline, not the final ranking engine. Keep
its evidence model when adding embeddings or LLM ranking so users can inspect why a repository
was selected and manually confirm the result.

