# Review Package

A review package (`kind: "review"`) is the platform putting one construction attempt's candidate
in front of the Team Leader as a copaw-native task of the Leader's own (D-3). `ack_task` and
`submit_task` are not role-restricted in copaw's taskflow; only `delegate_task` is Leader-only,
and the review never uses it. The Leader reviews; the verifier re-runs the tests afterwards
(D-10), so `ACCEPT` means "fit to enter independent verification", not "done".

## Where it is delivered

- `meta.json.assigned_to` is the Leader's resource name; `meta.json.room_id` is the **Leader's own
  room**, never the team room.
- The Leader receives review packages only in its own leader room. Workers never @mention the
  Leader: a construction spec tells the worker to post its `TASK_COMPLETED` line to `@admin` (the
  platform's sender identity) or to nobody. The wave-0 spike saw the Leader fall into identity
  confusion twice when @mentioned by a worker in the team room, and answer a structured review
  package in its own room in 70 seconds (S-4).
- The package carries `review/candidate.diff`, `review/changes.json`, `review/evidence.json` —
  the construction attempt's `candidate/` files verbatim — and `base/package.json` naming the
  attempt, the policy and the frozen commands. There is no `base/base.bundle`: the Leader does not
  build.

## Fixed sections of `spec.md`

Rendered from `src/repomesh/integrations/agentteams/task_package/spec_review.md.tpl`, in this
order:

1. Title `Review candidate <head_sha>: <task title>` and the header lines: review task id,
   the construction attempt under review (`review_of`) with its generation and RepoMesh task id,
   base and head commits, budget.
2. **What you are asked to do** — review only; complete the task through `ack_task` /
   `submit_task`; no `delegate_task`, no project creation, no @mention of the Worker.
3. **Frozen task the Worker had to implement** — the task instruction and the frozen acceptance
   criteria, plus the frozen test command(s) and the path rule (allowed / denied paths), exactly
   as the construction spec stated them.
4. **Candidate summary** — changed files (status, path) from `changes.json`; the tail of each test
   run from `evidence.json`.
5. **Review checklist** — implements the frozen task and nothing beyond it; changed paths inside
   the policy; evidence consistent with the diff; nothing that makes the candidate unsafe to
   verify or merge.
6. **How to answer** — the protocol below.
7. **Candidate diff** — `candidate.diff` embedded in a `diff` fence.

## Answer protocol

1. `taskflow(action="ack_task", payload={"taskId": "<attempt_id>"})`.
2. Read the diff and evidence.
3. Exactly one `taskflow(action="submit_task", ...)` with `deliverables: []` and a `summary` whose
   **first line is `VERDICT: <ACCEPT | REVISION | BLOCKED>`**, followed by 2–6 lines of reasons.
4. Optionally one room line, with no @mention: `REVIEW_DONE: <attempt_id> - VERDICT: <...>`. The
   platform ingests the verdict from `result.md`, not from the room.

The Leader does not run the tests, does not modify any file in the task directory, and does not
re-implement.

## Status mapping (fixed)

| `submit_task.status` | Verdict | Platform action |
| --- | --- | --- |
| `SUCCESS` | `ACCEPT` | Materialise the candidate and dispatch `repomesh-verifier` (D-11). |
| `SUCCESS_WITH_NOTES` | `ACCEPT` (with remarks) | Same; the remarks travel with the attempt record. |
| `REVISION_NEEDED` | `REVISION` | Open a new construction attempt (next generation) with the Leader's reasons appended to the spec. |
| `BLOCKED` | `BLOCKED` | The task is blocked and a human checkpoint opens (D-13). |

The `VERDICT:` line and the status must agree; when they do not, the status wins and the
disagreement is recorded on the attempt. A review that exceeds its budget (default 900 s) is not
skipped: the task is blocked and a human checkpoint opens (D-13).
