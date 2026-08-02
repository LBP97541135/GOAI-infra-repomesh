# Team Development Guide

## Starting a module

1. Read the module's `README.md` and `module.toml`.
2. Confirm the work belongs to the module; update the ownership map first if it does not.
3. Define public contracts before asking another module to consume new behavior.
4. Implement a vertical slice under `domain`, `application`, `ports`, `infrastructure`, and `api`
   only where those layers are needed.
5. Add behavior tests and adapter contract tests.

Do not create layers that contain no behavior. The directory convention describes dependency
direction, not a requirement to add empty folders.

## Pull-request boundaries

Prefer one owning module per pull request. A cross-module pull request is appropriate only when
introducing or migrating a public contract; it must identify the producer, consumers, rollout
order, and rollback behavior.

The composition root under `repomesh.bootstrap` is maintained by Platform. Feature modules must
not import it. New adapters are wired there only after their port and contract tests exist.

## Definition of done

- Observable behavior and module owner are stated.
- Domain invariants and failure paths are tested.
- Public contracts are version-compatible or have a migration plan.
- External side effects document idempotency, retry, timeout, and cancellation.
- Database migrations have forward and rollback notes.
- Audit events omit secrets and retain evidence identifiers.
- Ruff, behavior tests, contract tests, and architecture tests pass.
