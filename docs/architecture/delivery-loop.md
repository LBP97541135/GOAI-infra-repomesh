# Governed Delivery Loop

RepoMesh owns remote delivery after Runner execution; Coding Agents never receive GitHub write
credentials.

```text
ExecutionPlan completed
  -> collect frozen Runner commit/base/workspace evidence
  -> freeze test results, candidate Heads and environment into a Validation Snapshot
  -> create idempotent multi-repository ChangeSet
  -> platform Push with GitHub App installation token
  -> create ready Pull Requests
  -> persist signed Check Run and Pull Request Review observations
  -> claim and project observations with replay leases
  -> periodically reconcile PR, Check Run and Review snapshots from GitHub
  -> verify repository and exact candidate Head SHA
  -> evaluate CI, approval, recovery and dependency gates
  -> require an active READY governance decision for the exact Head SHA
  -> append an idempotent Head-bound Merge command in dependency order
  -> lease and dispatch the command to GitHub
  -> persist MERGE_REQUESTED without claiming remote completion
  -> reconcile live PR state until GitHub reports MERGED
  -> mark ChangeSet delivered
```

## Failure behavior

- A duplicate Runner completion, Push, PR request or webhook is idempotent.
- Merge commands survive restart and are reclaimed after an expired dispatch lease.
- Missed webhooks and process restarts recover from active GitHub reconciliation.
- Polling uses durable per-repository cursors, pagination, exponential failure backoff and GitHub
  `Retry-After`; repeated snapshots are content-addressed and harmless.
- A changed remote branch, PR Head SHA or CI Head SHA fails closed.
- A failed required check or requested-changes review blocks Merge.
- A BLOCKED, ROLLBACK_REQUIRED, missing or stale governance decision blocks Merge.
- Failed, expired, missing or Head-mismatched validation evidence blocks Merge.
- A downstream repository waits until every upstream repository is merged.
- GitHub remains the source of truth for PR and Merge state; RepoMesh stores governed observations.
- A successful Merge API response records only `MERGE_REQUESTED`; only a later GitHub observation
  records the merge commit and advances the ChangeSet.
- Existing recovery plans prevent Merge until their actions finish.
- Partial delivery runs a reverse-order recovery Saga; revert conflicts pause at a Worker task and
  never release later rollback actions.
- Webhook processing may request Merge only when `REPOMESH_DELIVERY_AUTO_ENABLED=true`.

## Required configuration

RepoMesh uses one platform GitHub App. Each customer organization installs that App on its selected
repositories; users do not create or enter a separate App ID. The token provider resolves the
installation from each repository and issues short-lived tokens.

Set `REPOMESH_GITHUB_APP_ID`, either `REPOMESH_GITHUB_APP_PRIVATE_KEY_FILE` (host process) or
`REPOMESH_GITHUB_APP_PRIVATE_KEY_BASE64` (container Secret), and
`REPOMESH_GITHUB_WEBHOOK_SECRET`. Configure named checks in
`REPOMESH_DELIVERY_REQUIRED_CHECKS`, keep at least one required approval, then set
`REPOMESH_DELIVERY_AUTO_ENABLED=true`. The reconciliation interval defaults to 60 seconds and is
configured with `REPOMESH_DELIVERY_RECONCILE_INTERVAL_SECONDS`.

The GitHub webhook URL is `/api/v1/delivery/github-webhook`. Subscribe the App to `check_run` and
`pull_request_review` events and grant Checks read, Contents write, and Pull requests write.
Operators can force immediate reconciliation with
`POST /api/v1/delivery/change-sets/{change_set_id}/reconcile` using the agent action bearer token.
