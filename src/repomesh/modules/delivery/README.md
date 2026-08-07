# Delivery

Owns multi-repository ChangeSets, repository changes, pull-request coordination, merge ordering,
release evidence, and rollback records. Coding agents cannot push, open PRs, or merge directly.

## Delivery flow

1. A validated commit from every repository is frozen into one ChangeSet.
2. Repository dependencies are topologically sorted into an enforced merge order.
3. The SCM adapter creates or reconciles one draft PR per repository; repeated observations are
   idempotent and the observed head SHA must equal the frozen candidate SHA.
4. Required CI checks move a repository delivery to `ready_to_merge`; failed checks block the
   ChangeSet and must return through a new task or validation snapshot.
5. A repository can merge only after all of its upstream repositories are recorded as merged.
6. The ChangeSet is delivered only when every repository is merged.

Merge is fail-closed. `evaluate_merge_gate` reports each blocker: missing CI evidence, unmerged
upstream repositories, earlier CI failures, or incomplete recovery actions. An SCM integration may
execute merge only after the gate returns `allowed=true`; dependency order is checked again when
the resulting merge commit is recorded.

GitHub events are verified and normalized by `integrations/scm` before entering this module. CI is
accepted only when its repository and full head SHA match the frozen ChangeSet candidate. Coding
agents never receive SCM push or merge credentials.

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
