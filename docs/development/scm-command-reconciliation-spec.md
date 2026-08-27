# SCM Command Reconciliation Specification

Status: accepted for implementation

## Goal

Converge RepoMesh control state with the external SCM after timeouts, missed webhooks, or process
crashes without blindly repeating destructive writes.

## Result Classes

- `completed`: remote state already matches the command target; acknowledge internally.
- `retryable`: target has not been reached and all immutable guards still match.
- `conflict`: head SHA, state, or repository identity differs; fail closed.
- `unknown`: provider cannot currently prove the state; retain retry evidence and back off.

## Command Rules

Merge checks the exact candidate head before calling GitHub. A merged pull request is completed
without another merge call. An open matching pull request is retryable. A changed head or closed
unmerged pull request is a conflict.

Undraft checks the exact head and open state. An already non-draft pull request is completed
without another mutation. A matching open draft is retryable.

Provider acceptance records only the governed request. Final merge completion and merge SHA still
come from webhook or polling reconciliation.

## Evidence

Every terminal command retains attempts, completion time, and bounded error detail. Lease owner
is operational metadata and is cleared when the command leaves processing.
