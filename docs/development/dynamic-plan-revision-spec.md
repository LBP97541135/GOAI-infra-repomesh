# Dynamic Plan Revision And Task Append Specification

Status: implementation complete; live PostgreSQL acceptance pending

## Problem

RepoMesh can replace affected work through the existing replan flow, but cannot append newly
discovered work to a running ExecutionPlan without superseding tasks and creating another plan.
Agents need a constrained append-only path for discoveries such as an additional repository change,
test task, or compatibility adapter.

## Boundary

- Dynamic append adds new repository-level plan items and dependencies.
- P0 allows only repositories not already present in the ExecutionPlan. Existing repository work
  uses full replan because delivery owns one candidate Head per repository.
- Existing plan items, completed batches, Task rows, and delivery evidence are immutable.
- Editing or removing existing work continues to use the full replan flow.
- Adding a repository outside the approved project topology requires an approval checkpoint before
  commit; preview remains side-effect free.

## Revision

Every accepted mutation creates a durable `execution_plan_revisions` row containing revision number,
base plan version, actor, reason, appended items, dependencies, previous/new batches, and status.
One idempotency key has one immutable meaning.

## DAG Rules

- Repository id is the P0 plan-item identity.
- Dependencies reference repository ids, including existing repositories.
- New items cannot become prerequisites of completed or already-running items.
- Cycles, missing dependencies, duplicate repositories, and self-dependencies are rejected.
- Topological batches are recomputed for pending/new items while the consumed prefix remains fixed.

## Runtime Behavior

- If the plan is completed or failed, append is refused; use a new/replanned execution.
- If the current batch is in progress, new ready work begins no earlier than the next batch.
- If the current batch has no assigned Task yet, recomputation may include new independent work in it.
- Completed work and its validation evidence remain valid.
- New dependents receive fresh Context Bundles and validation evidence through the ordinary flow.

## Done When

- concurrent append requests create one revision;
- completed/running work is not rewritten;
- cycles and backward dependencies fail closed;
- appended work is scheduled once after dependencies complete;
- revision history explains who added what and why.
