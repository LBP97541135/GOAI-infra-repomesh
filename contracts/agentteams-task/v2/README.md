# AgentTeams Task Package Contract v2

Status: draft (hosted-native wave 1). Producer: the RepoMesh task publisher
(`repomesh.integrations.agentteams.task_publishing`). Consumers: the copaw worker that
constructs, the copaw Leader that reviews, the RepoMesh shared-directory observer and the
RepoMesh verifier. Decisions referenced as D-n are in
`docs/development/agentteams-native-execution-mode-spec-20260902.md` §3; findings S-n are in
`docs/startup-records/2026-09-03-hosted-native-spike.md` §4.

v1 (`repomesh.agentteams-task.v1`: `spec.md`, `meta.json`, `manifest.json` under
`teams/<team>/shared/tasks/<task_id>/`) stays in force for the local-CLI construction mode and is
not described here. A publisher called without package inputs still writes v1.

## One attempt, one directory

A hosted-native attempt is one copaw-native task. Its directory under the team's shared storage
is `teams/<team>/shared/tasks/<attempt_id>/`, and **the directory name is the attempt id** (D-8).
The observer claims directories by name against its own `hosted_native_attempts` rows and never
reads `meta.json.repomesh` to do so (D-6, S-3). A second attempt at the same RepoMesh task is a new
directory with a new attempt id; a directory is never reused and never rewritten.

## Directory layout

| Path | Kind | Written by | Read by | Notes |
| --- | --- | --- | --- | --- |
| `spec.md` | both | platform | worker / Leader | Rendered from the construction or review template; the only prose the assignee gets. |
| `meta.json` | both | platform, then copaw | copaw; humans | copaw-native task record (`task_id` = attempt id, `assigned_to`, `room_id`, `status`, `depends_on`) plus a `repomesh` block that is a **publish-time snapshot only** — copaw rewrites the file from its own `TaskMeta` on `ack_task` / `submit_task` and the block disappears (S-3). No consumer may depend on it after publish (D-6). Schema: `meta.schema.json`. |
| `manifest.json` | both | platform | platform (conflict check) | Lists every published file with digest and size; `content_hash` covers all of them. Schema: `manifest.schema.json`. |
| `base/package.json` | both | platform | helper script, observer, verifier | The platform's control data. `base/` is excluded from copaw's push-back (S-9), so this file is the one reliable carrier of what the attempt was told. Schema: `package.schema.json`. |
| `base/base.bundle` | construction | platform | helper `init` | `git bundle` pinned at `base_sha` with `HEAD` and the branch ref (S-10). |
| `base/tools/repomesh-work.sh` | both | platform | worker (via the four command lines) | The helper, shipped with every package so it versions with the package. Documented in `helper-cli.md`. |
| `candidate/candidate.bundle` | construction | helper `bundle` | verifier | Exactly one commit on top of `base_sha`. |
| `candidate/candidate.diff` | construction | helper `bundle` | Leader (copied into the review package) | `git diff base_sha HEAD`. |
| `candidate/changes.json` | construction | helper `bundle` | Leader, verifier | Changed-file list. Schema: `candidate.schema.json#/$defs/changes`. |
| `candidate/evidence.json` | construction | helper `bundle` | Leader, verifier | Worker-side test evidence. Schema: `candidate.schema.json#/$defs/evidence`. |
| `result.md` | both | copaw (`submit_task`) | observer | copaw's own result file; the observer's event source for `submitted`. |
| `review/candidate.diff`, `review/changes.json`, `review/evidence.json` | review | platform | Leader | The construction attempt's `candidate/` files, copied verbatim. |

The task directory never contains a repository checkout, `.git`, dependencies or build caches:
the helper works in `<workspace_root>/<attempt_id>` (default `/work/<attempt_id>`), outside the
directory copaw synchronises (D-5).

## Lifecycle

```
notified → acknowledged → submitted → review → verifying → verified | failed | fenced
```

- `notified`: the package is published and the room notice sent.
- `acknowledged`: the assignee called `ack_task` (copaw rewrote `meta.json`). A claim, not a lease:
  the attempt budget (`budget_seconds`, from `notified`) is the only guaranteed interruption
  (D-12).
- `submitted`: `result.md` appeared with a `submit_task` status. `SUCCESS` /
  `SUCCESS_WITH_NOTES` carry the four `candidate/` deliverables; `BLOCKED` carries a reason and
  no deliverables.
- `review`: a review package was published to the Team Leader in the Leader's own room. The
  Leader answers with `submit_task` and its status maps to `ACCEPT` / `REVISION` / `BLOCKED`
  (`review.md`).
- `verifying`: the candidate was materialised as a worktree and a `repomesh-verifier` dispatch
  re-runs `test_commands` and applies the path policy (D-10, D-11, D-14).
- `verified` / `failed`: the verifier's terminal event. `fenced`: the attempt was superseded
  (budget expired, worker restarted, generation advanced) and anything it still writes is
  ignored.

## Fencing

- An attempt directory is created once and never reused (D-8). A publish that finds
  `manifest.json` with the same `content_hash` is a replay and writes nothing; one that finds a
  different `content_hash` is a conflict and is refused.
- Every event the observer ingests is keyed by attempt id and generation; an event for a
  generation other than the attempt's current one is refused (D-9). A worker that keeps writing
  into a fenced directory changes nothing on the platform side.
- `base/` is immutable for the life of the attempt and is not pushed back by copaw; the platform
  therefore trusts `base/package.json` and never `meta.json` for control data.

## Compatibility

Same rule as Runtime v1: optional additions to a v2 file are backward compatible and do not
change the `schema` identifier; consumers must ignore fields they do not know. An incompatible
shape — a renamed directory, a changed `content_hash` recipe, a required new file — is published
as a sibling `v3` directory, and this directory is immutable from then on.

## Files in this directory

| File | Content |
| --- | --- |
| `manifest.schema.json` | `manifest.json` |
| `meta.schema.json` | `meta.json` at publish time |
| `package.schema.json` | `base/package.json` |
| `candidate.schema.json` | `candidate/changes.json` and `candidate/evidence.json` (also `review/*.json`) |
| `helper-cli.md` | `repomesh-work.sh init|test|bundle|clean`, the four verbatim command lines, the Tool Guard fixture |
| `review.md` | The review package's fixed sections and the verdict mapping |

Producer and consumer tests: `tests/contracts/test_agentteams_task_v2_contract.py`,
`tests/integrations/agentteams/test_task_publishing.py`.
