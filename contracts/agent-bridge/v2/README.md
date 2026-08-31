# Agent Bridge Contracts — v2 (External Member)

v2 exists for exactly one reason: v1 describes a Worker and nothing else, and its schemas set
`additionalProperties: false`, so a role field cannot be added compatibly. v2 adds one required
field, `role`, to enrollment and binding, generalizing "external worker" to **external member**
(adjudication D-11): the same Bridge process can now serve a RepoMesh `worker` or a
`repository_leader`. Everything else — field names, shapes, patterns, semantics — is v1's,
verbatim; the contract test machine-checks that shared fields have not drifted.

**v1 is not superseded.** A worker-form Bridge running against v1 documents stays valid
indefinitely; deployed v1 consumers never need to learn v2. `room-observation.schema.json` has
no v2 — the room projection shape is role-independent and lives only in `../v1/`.

| Document | `schemaVersion` |
|---|---|
| `external-member-enrollment.schema.json` | `repomesh.agent-bridge.enrollment.v2` |
| `external-member-binding.schema.json` | `repomesh.agent-bridge.binding.v2` |

## The `role` field

- Allowed values: `worker`, `repository_leader`. **`organization_leader` is deliberately not
  representable**: the Organization Leader remains the existing AgentTeams Manager; RepoMesh
  provision rejects it server-side, and this schema cannot even express one.
- The binding's `role` is confirmed from RepoMesh's own agent directory, never echoed back from
  the enrollment. An enrollment/binding role mismatch is a stage-2 preflight failure.
- `workerAgentId` / `workerName` keep their historical v1 names for both roles (the same reason
  `REPOMESH_RUNNER_WORKER_TOKENS` keeps its name, adjudication D-6): renaming them would break
  every shared consumer for zero information. For a `repository_leader` they name the leader's
  AgentPrincipal id and AgentTeams resource name.

## Role-aware room allowlist

The binding's `allowedRoomIds` is role-aware and authoritative (the Bridge uses the
intersection with its enrollment list, exactly as in v1):

| role | authoritative rooms |
|---|---|
| `worker` | team room + worker DM |
| `repository_leader` | team room + leader DM |

## Role obligations (consumer side, enforced by Bridge + server, not by this schema)

- A `repository_leader` Bridge must refuse `--workspace-root` at the CLI boundary and never
  enters the Runner execution path; its coordination sessions receive a text-only fact package
  and no repository workspace (adjudication D-8). Server-side, `worker`-only hard checks on the
  execution path are unchanged (AC-02).
- A `worker` Bridge under v2 behaves exactly as under v1.

## Round-trip rules (machine-checked in `tests/contracts/test_agent_bridge_v2_contract.py`)

- **v1 → v2 upgrade**: set `schemaVersion` to the v2 constant and add `role: "worker"`. Nothing
  else changes. Every valid v1 document upgrades to a valid v2 document.
- **v2 → v1 downgrade**: only defined for `role: "worker"` — drop `role`, set the v1
  `schemaVersion`. A `repository_leader` document has **no v1 representation**; downgrading one
  is an error, not a lossy conversion.

## Fixtures

`fixtures/` holds the canonical documents shared by server-side (provision/preflight, PR 5.5A)
and Bridge-side (PR 8) test suites; both must consume these files rather than hand-rolling
copies:

| File | Meaning |
|---|---|
| `enrollment.worker.json` | valid v2 worker enrollment |
| `enrollment.repository-leader.json` | valid v2 repository leader enrollment |
| `binding.worker.json` | valid v2 worker binding |
| `binding.repository-leader.json` | valid v2 repository leader binding |
| `enrollment.invalid-role.organization-leader.json` | must be rejected: role outside the enum |
| `binding.invalid-room.malformed-room-id.json` | must be rejected: allowlist entry not a Matrix room id |

The invalid fixtures are load-bearing: PR 5.5A's server contract tests must show provision and
preflight rejecting them (Organization Leader → 409; malformed rooms → validation failure), and
the enrollment/binding **role mismatch** case (a `worker` enrollment answered by a
`repository_leader` binding, or vice versa) must be a preflight failure — that one is a
cross-document invariant, so it lives in the consuming test suites, not in a single fixture.

All v1 interface semantics (validation stages, trust model, idempotency, recovery, scope,
isolation, liveness, lifecycle) apply to v2 unchanged — see `../v1/README.md`. This directory
was frozen as part of the wave-0 contract baseline on 2026-08-28; field changes after the
freeze require a new sibling version, not edits here.
