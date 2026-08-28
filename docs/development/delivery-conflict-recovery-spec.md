# Delivery Base Drift And Conflict Recovery Specification

Status: implementation complete; live PostgreSQL and multi-project SCM acceptance pending

## Existing Guarantee

RepoMesh already topologically orders repositories, gates downstream merges on upstream completion,
and advances a durable merge cursor. This specification does not replace merge ordering.

## Problem

A correctly ordered candidate can still be based on an obsolete target branch because another
project or an earlier merge changed `main`. GitHub may report a changed PR base SHA or
`mergeable=false`. Today reconciliation skips such a PR without a durable explanation or repair
workflow.

## Conflict Case

Persist one active case per ChangeSet/repository with type `base_drift` or `content_conflict`, frozen
candidate Head, expected/observed Base, safe detail, repair Task id, status, timestamps, and version.
Cases are idempotent and survive API/Reconciler restarts.

## Behavior

- Base SHA drift or an unmergeable open PR creates/updates a Conflict Case.
- An active Case closes the Merge Gate independently of CI/review status.
- RepoMesh creates one conflict-resolution Worker Task in the exact project repository Team.
- The repair starts from the current target branch, preserves the intended change, runs repository
  verification, and produces a new candidate Head; shared history is never force-reset.
- Recording the new candidate Head resolves the old Case but invalidates prior CI, reviews,
  governance, and Validation Snapshot through existing Head-bound gates.
- A new Case may be opened if the revised candidate drifts again.
- Missing topology/Worker escalates to the existing human exception flow rather than retrying
  forever.

## Done When

- drift and unmergeable PRs are visible durable states;
- delivery cannot merge while a Case is active;
- reconciler replay creates one repair Task;
- revised Head resolves the Case and requires fresh validation/approval;
- ordered multi-repository delivery behavior remains unchanged.
