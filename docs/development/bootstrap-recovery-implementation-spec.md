# Bootstrap Recovery And Redaction Implementation Spec

Status: implemented and accepted for B8

## Outcome

Transient Docker, network, Controller, Matrix, storage, restart, and readiness failures receive
bounded automatic retries before the durable operation becomes `retryable_failure`. Crashes recover
through lease expiry. Logs and stored error details cannot contain known or token-shaped secrets.

## Retry Policy

Internal phase retry is allowed only for idempotent probes/actions:

- Docker availability probe;
- Controller health probe after installation;
- Matrix login;
- API restart command;
- API readiness verification.

Installer execution itself is attempted once per operation attempt. Default internal policy is
three attempts with delays of one and two seconds; tests inject zero delays. Internal retry never
changes the durable operation attempt count.

## Failure Classes

- `waiting_for_user`: missing model credential only;
- `retryable_failure`: Docker unavailable, pull/install failure, Controller/Matrix/storage/runtime
  write/restart/readiness failure;
- `terminal_failure`: unsafe target selection or invalid runtime schema indicating tampering or an
  invariant violation.

Multiple or mismatched API container labels are terminal safety failures. Zero matches remains
retryable during restart races.

## Redaction

Add a bootstrap redactor that replaces explicitly registered values, Bearer tokens, common JSON/env
secret fields, and long Base64/Base64URL token shapes. Diagnostics are redacted and bounded before
storage. Stable operation IDs, phases, error codes, HTTP statuses, and service names remain visible.

## Lease Recovery

- heartbeat renewal failure cancels the executor;
- no terminal transition is written after ownership is lost;
- expired running lease is reclaimable with attempt incremented once;
- retry endpoint refuses a live lease;
- SIGTERM leaves the operation reclaimable when a bounded action cannot finish.

## Tests

- retry succeeds on second/third probe without incrementing operation attempt;
- final transient failure maps to retryable state;
- installer is not internally repeated;
- unsafe target mismatch is terminal;
- heartbeat loss cancels executor and writes no stale transition;
- corpus scan covers model, Controller, Matrix, MinIO, internal and Bearer sentinels;
- logs, error fields, image history, and container argv contain no sentinel.

## Done

B8 is complete when retry/redaction tests pass, production skip-path logs pass a sentinel scan, and
lease-loss tests prove no stale owner can complete an operation.
