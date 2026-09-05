# Implement multi-currency quote() for repomesh-e2e-pricing-core

- Attempt: `ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a` (generation 1 of RepoMesh task `b6e0bc59-255a-4edc-a186-47f5eeb3050c`)
- Repository: `repomesh-e2e-pricing-core`, base commit `882231dd887688a986b0faec656a90d29141406c`
- Budget: 45 minutes from acknowledgement

## Current task

Modify quote() to accept a mandatory currency parameter (ISO 4217 code). Implement currency-specific rounding: for zero-decimal currencies (e.g., JPY), round the amount to the nearest integer; for other currencies, use standard decimal places. Update all unit tests to cover multi-currency scenarios, including JPY and a standard decimal currency, and ensure existing tests pass.

## Acceptance criteria (frozen by the Manager; do not change them)

- Code compiles without errors.
- Existing tests pass: every test in `tests/test_quote.py` as shipped at the base commit must pass unchanged. You may add tests; do not delete or weaken existing ones.
- Modify quote() to accept a mandatory currency parameter (ISO 4217 code). Implement currency-specific rounding: for zero-decimal currencies (e.g., JPY), round the amount to the nearest integer; for other currencies, use standard decimal places. Update all unit tests to cover multi-currency scenarios, including JPY and a standard decimal currency, and ensure existing tests pass.
- The frozen test command `python scripts/run_tests.py` exits 0 inside the workspace. The RepoMesh verifier re-runs exactly this command on your candidate.
- Only files under `src/**`, `tests/**` and `README.md` may change. Never touch `.github/**`.

## How to work (read this whole section before doing anything)

The source code ships inside this package (`base/base.bundle`). You need no network access, no MCP tool, and no repository checkout other than the one the helper creates. Do not call `repomesh-task-control` or any other MCP server for this task.

1. Accept the task: `taskflow(action="ack_task", payload={"taskId": "ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a"})`.
2. From the task directory (`shared/tasks/ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a/`) create the workspace:

       bash base/tools/rm-work.sh init

   This clones the shipped source into a local workspace outside the task directory and checks out the base commit. Make all code changes inside that workspace only. Do not copy the repository into the task directory and do not put source code under `workspace/`.
3. Implement the change, then run the frozen tests:

       bash base/tools/rm-work.sh test

   Repeat edit / test until it prints `all tests passed`.
4. Produce the candidate:

       bash base/tools/rm-work.sh bundle

   This commits your work as exactly one commit on top of the base commit and writes four files into `candidate/` inside this task directory: `candidate.bundle`, `candidate.diff`, `changes.json`, `evidence.json`. Do not create or edit those files by hand.
5. Submit the result with exactly these deliverables:

       taskflow(action="submit_task", payload={
         "taskId": "ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a",
         "status": "SUCCESS",
         "summary": "<what you changed and the test outcome>",
         "deliverables": [
           "shared/tasks/ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a/candidate/candidate.bundle",
           "shared/tasks/ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a/candidate/candidate.diff",
           "shared/tasks/ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a/candidate/changes.json",
           "shared/tasks/ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a/candidate/evidence.json"
         ]
       })

   Use `SUCCESS_WITH_NOTES` when the tests pass but something needs attention, and `BLOCKED` (reason in `summary`, no deliverables) when you cannot finish.
6. Then, in this room, @mention your coordinator with `TASK_COMPLETED: ca0ef2b0-a6c7-4d03-a0e3-b7bf13aef13a - <one line>`.

## Rules

- There is no git remote. Never push anywhere; the candidate leaves the container only through `submit_task`.
- Do not run `git` against anything except the workspace the helper created, and never run `git push`, `git remote add`, or `git clone` yourself.
- Do not modify `spec.md`, `meta.json`, `manifest.json`, or anything under `base/`.
- Do not paste container absolute paths, tokens, or keys into chat.
- Post one short `Progress:` line in this room after `init` succeeds and one after the first green test run. No other progress messages are needed.
