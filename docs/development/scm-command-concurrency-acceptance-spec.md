# SCM Command Concurrency Acceptance Specification

Status: complete

## Result

The PostgreSQL concurrency, expiry reclamation, stale-owner fencing, renewal, and migration
scenarios passed on 2026-08-27. Dispatcher contract tests verified a single provider mutation from
two concurrent dispatcher instances. The full repository regression completed with 1349 passed
and 19 environment-dependent skips.

## PostgreSQL Matrix

- 2, 8, and 32 concurrent claimers receive one command once.
- Multiple commands are distributed without duplicates or omissions.
- A live lease is skipped.
- An expired lease is reclaimed with a higher attempt and fencing version.
- The previous owner cannot renew, accept, or fail after reclamation.
- A current owner can renew without changing the fencing version.
- Commands at the attempt limit remain unclaimed.

## Dispatcher Matrix

- two dispatchers produce one provider mutation for one live claim;
- provider success followed by acknowledgement failure converges from remote state;
- merged and already-undrafted states do not repeat mutations;
- remote head drift fails closed;
- cancellation stops renewal and leaves recovery to lease expiry.

## Migration Matrix

- upgrade initializes lease fields as null;
- downgrade removes only the new lease fields and indexes;
- upgrade, downgrade to 0037, and re-upgrade pass on PostgreSQL.

## Pass Condition

The database is the sole ownership arbiter, a stale dispatcher cannot acknowledge work, and
external state converges without duplicate effective SCM changes.
