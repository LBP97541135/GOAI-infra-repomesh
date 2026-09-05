# Review candidate `${head_sha}`: ${title}

- Review task: `${attempt_id}` (assigned to you, the Team Leader)
- Reviews construction attempt: `${review_of}` (generation ${generation} of RepoMesh task `${task_id}`)
- Base commit: `${base_sha}`; candidate head: `${head_sha}` (exactly one commit on top of the base)
- Budget: ${budget_minutes} minutes from acknowledgement

## What you are asked to do

You are the first reviewer of a Worker's candidate result. You review; you do not fix, re-implement, or re-run anything.
The RepoMesh verifier re-runs the frozen tests independently after your verdict, so your `ACCEPT` means
"good enough to enter independent verification", not "task complete".

This task is completed **through the task protocol**: acknowledge it with `taskflow(action="ack_task")` and
return your verdict with `taskflow(action="submit_task")` as described below. `ack_task` and `submit_task` are
allowed for a Leader on a task that is assigned to the Leader, which this one is (`meta.json.assigned_to` is you).
Do not use `delegate_task`, do not create a project, and do not @mention the Worker.

## Frozen task the Worker had to implement

${instruction}

Frozen acceptance criteria:

${acceptance}
- The frozen test command(s) ${test_commands} exit 0.
- ${path_rule}

## Candidate summary

Changed files (status, path):

${changed_files}

Worker-reported test evidence (`review/evidence.json`, last lines of each command):

${tests_block}

## Review checklist

1. Does the diff implement the frozen task above — every acceptance criterion, and nothing beyond it?
2. Do the changed paths stay inside the allowed paths and outside the denied ones?
3. Is the evidence consistent with the diff (the tests that were run actually exercise the change)?
4. Anything that would make the candidate unsafe to verify or merge (deleted tests, weakened assertions, unrelated changes)?

The full diff is embedded below and is also available in this task directory as `review/candidate.diff`,
`review/changes.json`, `review/evidence.json`.

## How to answer

1. `taskflow(action="ack_task", payload={"taskId": "${attempt_id}"})`
2. Read the diff and evidence (below, or the files under `review/`).
3. Submit exactly one verdict:

       taskflow(action="submit_task", payload={
         "taskId": "${attempt_id}",
         "status": "<SUCCESS | SUCCESS_WITH_NOTES | REVISION_NEEDED | BLOCKED>",
         "summary": "VERDICT: <ACCEPT | REVISION | BLOCKED>\n<your reasons, 2-6 lines>",
         "deliverables": []
       })

   Mapping (fixed): `SUCCESS` = `ACCEPT` (send to independent verification); `SUCCESS_WITH_NOTES` = `ACCEPT` with remarks;
   `REVISION_NEEDED` = `REVISION` (a new construction attempt will be opened with your reasons);
   `BLOCKED` = `BLOCKED` (you cannot judge; say why).
   The first line of `summary` must be `VERDICT: ...`.
4. Optionally, reply in this room with one line and no @mention: `REVIEW_DONE: ${attempt_id} - VERDICT: <...>`.

Do not modify any file in this task directory except through `submit_task`. Do not run the tests yourself; the verifier does that.

## Candidate diff (`base..head`)

```diff
${diff}
```
