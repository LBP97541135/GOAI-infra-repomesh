# Decision Chain

Read-side projection module for the decision-chain contract (`docs/contracts/decision-chain-v0.1.md`).
Owns nothing about how a decision is made — the producers
(`repository_intelligence`, `task_orchestration`, `delivery`) are the source of
truth. This module subscribes to their events and keeps one read-only chain
view: **who decided what, in what order, for which requirement, affecting which
repositories**.

## The chain

One requirement (`project_id`, the E1 root) walks through five decision steps:

```
classification → confirmation → integration → task → pr
```

Each step lands as one `decision_chain_nodes` row (a "decision sheet"): chain
fields + a lightweight `payload_summary` + `evidence_refs` pointers. Full
payloads stay in the producer modules (red line 2 — no double-write).

## Event source

The five chain events (`ClassificationDecided`, `ConfirmationDecided`,
`IntegrationDecided`, `TasksPlanned`, `PullRequestObserved`) are emitted by the
producers and persisted to the platform `audit_events` table (via the shared
`EventEnvelope` shape). `PostgresDecisionEventSource` reads exactly those event
types from that table; the projector then maps each envelope into a decision
sheet and `DecisionChainStore.append` upserts it.

## Projection rules

- **Idempotent** — `event_id` is UNIQUE; a replayed event returns the existing
  row.
- **Versioned** — the first event of a step is version 1; later events of the
  same step (re-adjustment, redo) increment the version instead of overwriting.
- **Chain-linked** — `upstream_ref` points at the newest node of the previous
  step in the same project (classification roots the chain with NULL).
- **No guessing** — an event without `organization_id` cannot prove its
  ownership, so the projector skips it (red line 7).
- **Honest tracing** — `trace` returns what was projected; a step that never
  produced a node simply is not listed, and legacy rows (Phase 7 backfill
  later) surface as `legacy_gaps` rather than being papered over.

## Boundaries

- Reads only `platform.audit_events` (shared platform table) and its own
  `decision_chain` schema. It never touches a producer module's schema.
- The requirement text for `trace` comes through the `RequirementReader` port,
  wired in the composition root to `PlanSnapshotStore`.
- `find_similar_structural` (Phase 4) keeps the same read-only discipline.
