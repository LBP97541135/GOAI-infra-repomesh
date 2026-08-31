# SCM Command Atomic Leasing Specification

Status: accepted for implementation

## Problem

`SCMCommandDispatcher` currently lists dispatchable commands and claims each id in a second
transaction. Concurrent dispatchers can observe the same version before either update commits.
The database must decide ownership before any GitHub side effect begins.

## Guarantees

- PostgreSQL atomically returns each command to at most one live lease owner.
- A claim records `lease_owner`, `lease_expires_at`, increments `attempts`, and increments `version`.
- `version` is the fencing token returned with the claim.
- Renew, accept, and fail require the same owner and fencing version.
- An expired owner cannot complete a command after another dispatcher reclaims it.
- Commands reaching `max_attempts` are not reclaimed automatically.
- GitHub remains the source of truth for pull-request state.

## Acquisition

The store exposes `claim_batch(owner, lease_seconds, max_attempts, limit)`. PostgreSQL selects
eligible pending, failed, or expired-processing rows in creation order with
`FOR UPDATE SKIP LOCKED`. Selection and transition to processing happen in one transaction.

The in-memory store provides the same behavioral contract for unit tests. The old
`list_dispatchable -> claim(id)` path is removed from the dispatcher.

## Ownership And Fencing

Every claimed view carries an owner, expiry, and version. Mutating a processing command requires:

```text
id = command_id
status = processing
lease_owner = owner
version = fencing_version
```

Failure to match is an ownership conflict, never a successful no-op. Accept and fail clear the
lease. Renew extends only an unexpired lease and preserves the fencing version.

## External Side Effects

Database transactions cannot make GitHub calls exactly once. RepoMesh therefore provides
at-least-once delivery with convergence:

1. claim one durable command;
2. read the current pull-request state;
3. if the target state already exists, record the internal result without repeating the write;
4. otherwise make the idempotent or state-guarded provider call;
5. accept only under the original lease and fencing version;
6. let reconciliation recover calls that reached GitHub before acknowledgement.

Head drift, closed pull requests, or another incompatible remote state fail closed.

## Non-Goals

- Distributed transactions with GitHub.
- Holding a database transaction open during a network call.
- Allowing browser or Agent input to select commands or shell arguments.

## Done When

- concurrent PostgreSQL claims return a command once;
- expired claims are recoverable and stale owners are fenced out;
- Dispatcher uses only atomic batch claims;
- remote-already-completed recovery remains idempotent;
- lease behavior is covered by unit and real PostgreSQL tests.
