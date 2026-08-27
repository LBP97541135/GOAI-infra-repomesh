# Worker Failure And Reassignment Specification

Status: planned after execution reservation

## Goal

Recover a task whose Worker or Runner disappeared without allowing the old execution to publish a
late result.

## Policy

- A healthy lease remains owned by its current Worker.
- An expired preparing reservation is retried from clean preparation.
- An expired running reservation first checks durable Runner/session state.
- A resumable native session stays with the same execution attempt.
- A non-resumable attempt is closed and a new assignment attempt selects a healthy Worker from the
  same project repository Team.
- Every reassignment records old Worker, new Worker, reason, actor, and fencing version.
- Exhausted retries create an exception checkpoint for human action.

Task assignee changes require an explicit audited reassignment operation; reservation recovery must
not silently mutate task ownership.
