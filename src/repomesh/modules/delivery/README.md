# Delivery

Owns multi-repository ChangeSets, repository changes, pull-request coordination, merge ordering,
release evidence, and rollback records. Coding agents cannot push, open PRs, or merge directly.

## Delivery flow

1. Completion of an execution plan freezes every Runner commit into one ChangeSet.
2. Repository dependencies are topologically sorted into an enforced merge order.
3. The platform Pushes each frozen commit and creates or reconciles one ready PR per repository;
   repeated observations are
   idempotent and the observed head SHA must equal the frozen candidate SHA.
4. Signed GitHub Webhooks first append the raw external fact to `delivery.scm_observations`.
   A leased processor attaches normalized Required Check and PR Review observations to the exact
   Head SHA; failed or interrupted processing is replayed after restart.
5. Required checks and approvals move a repository delivery to `ready_to_merge`; failed checks or
   requested changes block it.
6. Before Merge, RepoMesh actively reconciles the PR Head, Draft state and mergeability.
7. A repository can merge only after all of its upstream repositories are recorded as merged.
8. The ChangeSet is delivered only when every repository is merged.

GitHub accepting a Merge request moves a repository only to `merge_requested`. RepoMesh records
`merged` and the merge commit SHA only after reconciliation observes the remote PR in the merged
state. Replayed Webhooks cannot submit another Merge while that confirmation is pending.

Merge is fail-closed. `evaluate_merge_gate` reports each blocker: missing CI or review evidence, unmerged
upstream repositories, earlier CI failures, or incomplete recovery actions. An SCM integration may
execute merge only after the gate returns `allowed=true`; dependency order is checked again when
the resulting merge commit is recorded.

GitHub events are verified and normalized by `integrations/scm` before entering this module. CI is
accepted only when its repository and full head SHA match the frozen ChangeSet candidate. Coding
agents never receive SCM push or merge credentials.

The Observation ledger is append-only: duplicate provider event identities must have the same
payload hash, processing failures retain their raw JSON and error evidence, and a stale processing
lease can be reclaimed. Webhook and Poller acquisition share this contract.

Operators can trigger the same idempotent recovery immediately with
`POST /api/v1/delivery/change-sets/{id}/reconcile`. Automatic mode also scans every unfinished
ChangeSet at `REPOMESH_DELIVERY_RECONCILE_INTERVAL_SECONDS`; failures retry on the next interval.

Automatic delivery is disabled until `REPOMESH_DELIVERY_AUTO_ENABLED=true`, at least one named
`REPOMESH_DELIVERY_REQUIRED_CHECKS` entry, one required approval, a GitHub App, and a webhook
secret are configured. This avoids treating an arbitrary green check as approval to merge.
The Webhook observation processor also receives this flag explicitly; merely configuring a GitHub
App does not grant it permission to request Merge.

## GitHub App credentials

Production SCM writes use a GitHub App rather than a personal access token. Configure
`REPOMESH_GITHUB_APP_ID` and `REPOMESH_GITHUB_APP_PRIVATE_KEY_FILE`; the latter is a reference to a
mounted PEM secret, not key contents. RepoMesh signs a nine-minute App JWT, resolves the repository
installation, and caches its short-lived Installation Token with a five-minute refresh margin.
Concurrent requests for the same repository share one refresh operation. Tokens remain in memory
only and are cleared during shutdown.

## Recovery and compensation

- A Runner failure without a confirmed native session creates a fresh retry action.
- An interrupted Runner with a confirmed native session creates a resume action.
- An unmerged PR is compensated by closing it while retaining evidence.
- A merged repository is never force-reset. Compensation creates a revert PR.
- Partial multi-repository delivery compensates in reverse dependency order and requires a new
  validation snapshot before delivery can resume.
- Recovery plans are durable control records. SCM/CI adapters execute actions separately and must
  report their result idempotently.
