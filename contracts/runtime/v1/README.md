# RepoMesh Runtime Contract v1

This package is the language-neutral boundary between the RepoMesh product control plane,
AgentTeams runtime control plane, and RepoMesh Runner execution plane.

## Messages

- `runner-task.schema.json`: immutable coding task accepted by a Runner.
- `runner-event.schema.json`: ordered Runner observation delivered back to RepoMesh.
- `runtime-metadata.schema.json`: RepoMesh identity and correlation metadata attached to an
  AgentTeams runtime projection.

Transports may be HTTP, Matrix, a queue, or an object-store notification. Transport choice does not
change the envelope. Receivers deduplicate tasks by `idempotencyKey` and events by `eventId`.

The contract contains credential references, never secret values. Context and artifacts are
immutable URI plus SHA-256 references. Matrix messages are not command or event persistence.

## Runtime definitions

- `worker-runtime.md`: the `repomesh-runner` worker runtime — entrypoint, environment, configuration
  tree, heartbeat, termination, and storage boundary of an AgentTeams worker that runs a Runner.

## Compatibility

Additive optional fields are backward compatible within v1. Removing a field, renaming a field,
changing its meaning, or adding a required field requires a new contract version. Producers must
continue emitting the previous version until every deployed consumer supports the replacement.
