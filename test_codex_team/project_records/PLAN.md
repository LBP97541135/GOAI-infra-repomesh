# Todo List Frontend Delivery Plan

## Stage record

- Time: 2026-08-10T10:15:42-07:00
- Role: leader_A (frontend owner)
- Input: `SPEC.md`, manager's mandatory workflow, current worktree status
- Decision: enforce sequential gates `leader alignment -> PM -> developer -> reviewer -> leader cross-check -> commit`
- Output: this execution plan
- Validation: no PM, developer, or reviewer implementation work was dispatched before this plan was written

## Ownership and stage gates

1. **Leader contract gate (complete)**
   - leader_A authors `SPEC.md` and `PLAN.md`.
   - leader_A and leader_B explicitly align API contract, ports, CORS, run commands, and joint acceptance.
   - leader_A records the confirmed outcome in `DECISIONS.md`.
   - Exit condition: written confirmation from leader_B and synchronized contract text.

2. **PM gate (complete)**
   - leader_A sends the complete leader-authored documents to worker_A_pm.
   - PM decomposes work, identifies risks, and produces an acceptance checklist in the records.
   - PM reports decisions and outputs to leader_A.
   - Exit condition: leader_A verifies the PM record is concrete and consistent with the confirmed contract.

3. **Development gate (complete; accepted at 2026-08-10T10:44:17-07:00)**
   - Only after PM approval, leader_A sends all records to worker_A_dev.
   - Developer implements the frontend, tests, README, and writes actual commands/results/deviations to `DEVELOPMENT_LOG.md`.
   - Exit condition: developer reports implementation complete with reproducible test evidence.

4. **Independent review gate (round 1 rejected; round 2 APPROVED at 2026-08-10T11:19:37-07:00)**
   - Only after development completion, leader_A sends the implementation and records to worker_A_rev.
   - Reviewer independently inspects task scope and runs tests, recording findings in `REVIEW.md`.
   - If review fails, leader_A routes precise findings back to developer and repeats independent review.
   - Exit condition: reviewer explicitly approves with evidence.

5. **Leader integration gate (complete at 2026-08-10T11:23:49-07:00)**
   - leader_A reruns frontend tests and inspects the final diff.
   - leader_A and leader_B perform a second contract/runbook consistency check.
   - Exit condition: frontend and backend instructions and contracts match.

6. **Commit and report gate (authorized at 2026-08-10T11:23:49-07:00)**
   - Commit only task-related paths under `test_codex_team/`.
   - Do not stage, overwrite, or clean pre-existing unrelated changes.
   - Report roster flow, decisions, review result, commands/results, changed files, commit hash/subject, and task-related uncommitted status to manager.

## Planned verification

- Frontend unit tests: `node --test test_codex_team/tests/*.test.mjs` (final path may be narrowed by developer and must be recorded).
- Repository checks required before PR-level delivery: `ruff check .` and `pytest`.
- Static frontend smoke: serve `test_codex_team/frontend/` at port `5173`, open it against backend at port `8000`, and exercise list/add/toggle/delete plus loading/empty/error states.
- Git scope: compare `git status --short`, `git diff -- test_codex_team`, and staged file list before commit.

## Known risks before PM analysis

- Contract drift between independently implemented repositories.
- CORS mismatch between `localhost` and `127.0.0.1` origins.
- Browser-specific behavior not covered by pure Node tests.
- A backend not yet running can make UI error behavior appear like an implementation failure.
- Existing unrelated untracked directories must not be staged accidentally.

## PM stage record

- Time: 2026-08-10T10:17:18-07:00
- Role: worker_A_pm (requirements decomposition, plan, progress/risk coordination)
- Input: confirmed `SPEC.md`; the leader-authored delivery plan; `DECISIONS.md` Decisions 001-003; leader_A's instruction that the contract is frozen and the development gate remains closed
- Decision: preserve the confirmed HTTP contract unchanged and organize the frontend work as a sequential, file-scoped package with explicit verification evidence. No developer or reviewer work is authorized by this record; leader_A remains the gate owner.
- Output: the deliverable breakdown, dependency order, risk register, executable acceptance checklist, developer completion definition, and independent-review input bundle below
- Validation: cross-checked every route, status, JSON shape, origin, port, run command, and dependency constraint against `SPEC.md` and `DECISIONS.md`; confirmed that only the three project-record files currently exist under `test_codex_team/`; checked the worktree status and preserved unrelated untracked `frontend-prototype/` and `output/`

No new product or cross-team contract decision was introduced during PM analysis, so `DECISIONS.md` is unchanged.

## File-level work breakdown and dependency order

| Order | Owner | Deliverable | Required content | Depends on / exit evidence |
| --- | --- | --- | --- | --- |
| 0 | leader_A | Gate authorization | Verify this PM record against the frozen contract and explicitly open the development gate. | Decisions 001-003 and this PM record; no implementation starts before written authorization. |
| 1 | worker_A_dev | `test_codex_team/project_records/DEVELOPMENT_LOG.md` (opened at start, completed at handoff) | Record environment preflight, exact commands/results, deviations, backend availability, smoke evidence, and final changed-file list. | Order 0; initial entries show `node --version`, the chosen static-server command, and confirmation that no package install is required. |
| 2 | worker_A_dev | `test_codex_team/frontend/api.mjs` | Own base-URL configuration, all four todo API calls, exact methods/headers/bodies, status checks, JSON/shape validation, `204` handling, and actionable error extraction from `{ "detail": string }`. Allow test injection of the request implementation. | Order 1; API tests can exercise request construction without a browser or live backend. |
| 3 | worker_A_dev | `test_codex_team/frontend/state.mjs` | Pure helpers for normalizing/validating todo data and immutable add/replace/remove transitions; no DOM, network, or backend imports. | Order 1; deterministic pure-function tests. May proceed in parallel with Order 2 after authorization. |
| 4 | worker_A_dev | `test_codex_team/frontend/index.html` and `test_codex_team/frontend/styles.css` | Semantic page/form/list structure; explicit loading, empty, and actionable error regions; accessible labels/native controls; responsive readable styling; ES-module entry point. | Orders 2-3 contract surfaces understood; static inspection and browser render. |
| 5 | worker_A_dev | `test_codex_team/frontend/app.mjs` | Initial load, add, toggle, delete, rendering, last-known-good state preservation, per-operation in-flight guards, control disabling, and deterministic loading/empty/error transitions. | Orders 2-4; all user-visible behaviors wired only through `api.mjs` and `state.mjs`. |
| 6 | worker_A_dev | `test_codex_team/tests/api.test.mjs` and `test_codex_team/tests/state.test.mjs` | Automated coverage for exact requests/status handling/response validation/error detail and core state transitions, including preservation/non-mutation behavior. Add focused orchestration tests only if logic extracted from `app.mjs` can be tested without browser dependencies. | Orders 2-5; `node --test test_codex_team/tests/*.test.mjs` exits 0. |
| 7 | worker_A_dev | `test_codex_team/README.md` | Exact frontend serve/test commands, URL, backend dependency and commands, configurable backend URL mechanism, end-to-end smoke procedure, expected states, and troubleshooting for backend/CORS failures. | Orders 2-6; a fresh reader can reproduce startup and checks without guessing. |
| 8 | worker_A_dev | Completed `DEVELOPMENT_LOG.md` and handoff | Actual command outputs/summaries, smoke matrix, known limitations/deviations, scoped Git status, and complete review input bundle. | Orders 1-7; satisfies the developer completion definition below. |
| 9 | worker_A_rev | `test_codex_team/project_records/REVIEW.md` | Independent contract/spec review, rerun evidence, accessibility/static review, Git-scope check, findings with severity and file location, and explicit approve/reject conclusion. | Order 8 and leader_A review dispatch; reviewer receives the bundle below and does not rely only on developer claims. |

Implementation details may be simplified by worker_A_dev only if all named responsibilities remain easy to locate and test. Renaming, combining, or adding files is a reported plan deviation; changing the confirmed HTTP contract requires stopping and escalating to leader_A rather than editing it locally.

## PM risk register

| Risk | Trigger signal | Mitigation / required response | Owner |
| --- | --- | --- | --- |
| Frontend/backend contract drift | Any route, method, status, JSON field/type, error shape, origin, or port differs from Decision 001 or observed backend behavior. | Stop integration; capture the exact request/response; do not add compatibility guesses; leader_A coordinates written reconfirmation with leader_B and updates records before work resumes. | leader_A |
| CORS origin/preflight mismatch | Browser console reports a CORS failure, `OPTIONS` fails, or one of `localhost:5173` and `127.0.0.1:5173` behaves differently. | Test both confirmed origins; separate CORS evidence from UI logic failures; send the failing origin/method/header and browser evidence to leader_A for cross-team resolution. | worker_A_dev, coordinated by leader_A |
| Backend unavailable is misdiagnosed as frontend failure | `/health` is unreachable or backend is not running when smoke testing begins. | Check and record `/health` before joint smoke; still verify that the UI shows an actionable network error; label blocked integration checks distinctly from failed frontend checks. | worker_A_dev |
| Duplicate or racing mutations corrupt visible state | Rapid repeated add/toggle/delete creates duplicate requests, stale replacement, or an unexpected re-enabled control. | Use an add-level and affected-item-level in-flight guard; disable only relevant controls; test rapid/repeated intent where feasible; refresh from the server only through an explicit, documented recovery path. | worker_A_dev |
| Failed mutation destroys last-known-good data | A rejected `POST`, `PATCH`, or `DELETE` clears the list or leaves an optimistic state as if successful. | Commit state transitions only from successful validated responses (or explicitly roll back); retain the last valid list; expose retry guidance in the error region. | worker_A_dev |
| Malformed or unexpected response crashes rendering | Missing `todos`, invalid Todo fields, non-JSON success, invalid `{detail}`, or a body on unexpected status causes an uncaught exception. | Centralize validation/error normalization in `api.mjs`; cover invalid success/error responses in tests; render a safe actionable fallback. | worker_A_dev |
| Pure Node tests miss DOM/browser behavior | Unit tests pass while focus, keyboard use, state visibility, module loading, or CORS fails in a browser. | Require a real-browser smoke covering all operations and visible states; reviewer repeats critical browser/static accessibility checks independently. | worker_A_rev |
| Accessibility regresses during dynamic rendering | Unlabeled input/control, inaccessible toggle state, no announced status/error, lost focus, or keyboard-only blockage is observed. | Use semantic form/list/native controls; provide programmatic names and status/error live regions; perform keyboard-only and focus checks before handoff. | worker_A_dev, verified by worker_A_rev |
| Environment/dependency assumption fails | Node is absent/too old, static server command fails, or implementation introduces a package install. | Record versions and commands before coding; stay with browser APIs and Node built-ins; escalate any required dependency deviation before adding it. | worker_A_dev |
| Repository-wide checks fail for unrelated baseline reasons | `ruff check .` or `pytest` fails outside `test_codex_team/`, or no relevant Python tests are collected. | Preserve complete failure evidence and path attribution; do not modify unrelated modules to make the checks green; leader_A decides whether the failure blocks delivery. | leader_A |
| Unrelated user work is staged or modified | Git status/diff includes `frontend-prototype/`, `output/`, or any path outside the task-owned `test_codex_team/`. | Inspect status before and after work; stage explicit task files only; never clean or overwrite unrelated paths; reviewer verifies staged scope independently. | worker_A_dev, verified by worker_A_rev |

## Executable acceptance checklist

Each item must be marked with evidence in `DEVELOPMENT_LOG.md` and independently sampled or rerun in `REVIEW.md`. An unchecked mandatory item prevents approval unless leader_A records a specific accepted deviation.

### Functionality

- [ ] Starting with a reachable backend loads `GET /api/todos` once and renders every returned Todo title and completion state.
- [ ] Submitting a title containing non-whitespace text sends one create request and renders the returned Todo.
- [ ] Empty or whitespace-only titles are rejected in the UI without sending a request.
- [ ] Repeated submit attempts while create is in flight cannot create duplicate requests, and the add control communicates/reflects its disabled state.
- [ ] Toggling an incomplete or completed todo sends the inverse boolean and renders the backend-returned Todo on success.
- [ ] Deleting a todo removes only that todo after a successful `204` response.
- [ ] Multiple todos retain their IDs, titles, order, and unaffected completion state across add/toggle/delete transitions.

### HTTP contract

- [ ] Default base URL is exactly `http://localhost:8000`; any override mechanism is documented and does not silently change the default.
- [ ] Requests exactly match: `GET /api/todos`, `POST /api/todos` with `{ "title": string }`, `PATCH /api/todos/{id}` with `{ "completed": boolean }`, and `DELETE /api/todos/{id}`.
- [ ] JSON mutations send `Content-Type: application/json`; create accepts only `201`, list/toggle accept only `200`, and delete accepts `204` without attempting JSON parsing.
- [ ] List payload `{ "todos": [...] }` and every Todo `{ "id": string, "title": string, "completed": boolean }` are validated before entering UI state.
- [ ] Error `{ "detail": string }` is surfaced when available, while network, invalid JSON, invalid success payload, and unexpected-status failures receive a safe actionable fallback.
- [ ] Browser smoke from both `http://localhost:5173` and `http://127.0.0.1:5173` completes without a CORS or mixed-origin configuration error, or a backend-owned blocker is recorded with evidence.

### Loading, empty, and error states

- [ ] Initial fetch visibly shows loading, prevents contradictory empty content, and resolves deterministically to either list, empty, or error state.
- [ ] Successful load with zero todos shows an explicit empty-list message; that message disappears as soon as a todo exists.
- [ ] A failed initial load shows an actionable error rather than a false empty state or an uncaught error.
- [ ] A failed add/toggle/delete leaves the last-known-good list intact and re-enables affected controls for retry.
- [ ] An item-level mutation prevents repeated actions on that item without unnecessarily blocking unrelated todos.
- [ ] A later successful operation clears or updates stale error feedback so current state is not presented as failed.

### Accessibility and browser usability

- [ ] Document language, page title, main heading, form, input label, and submit button have semantic/programmatic meaning.
- [ ] Every toggle and delete control has an accessible name that identifies its todo; completion state is exposed through a native control or correct state semantics.
- [ ] Loading/status and error messages are available to assistive technology without stealing focus on every render.
- [ ] All operations are usable with keyboard only; visible focus is preserved, and deleting or adding an item leaves focus in a sensible place.
- [ ] Text, controls, completed styling, and focus indicators remain readable at narrow viewport widths and are not conveyed by color alone.

### Automated and manual verification

- [ ] `node --test test_codex_team/tests/*.test.mjs` exits 0 and covers exact request construction, status/body handling, response validation/error normalization, and pure state transitions.
- [ ] Tests use Node/browser built-ins only and do not require `npm install` or network access.
- [ ] The documented static-server command serves ES modules at port `5173` with no missing-file, syntax, or module-loading console errors.
- [ ] With backend health confirmed, browser smoke exercises list, empty, add, toggle, delete, duplicate-submit prevention, and at least one recoverable error path; actual results are recorded.
- [ ] Repository checks `ruff check .` and `pytest` are run before final delivery; any unrelated/baseline failure is preserved verbatim enough to attribute and is not concealed.

### Documentation and Git scope

- [ ] `test_codex_team/README.md` gives copy-paste frontend start/test commands, URL, backend start/test dependency, base-URL override, joint smoke steps, and CORS/backend troubleshooting.
- [ ] `DEVELOPMENT_LOG.md` lists actual changed files, environment versions, commands/results, smoke evidence, deviations, blockers, and residual risks; it does not claim unrun checks.
- [ ] `git status --short` is captured before handoff, and every task-owned file is under `test_codex_team/`.
- [ ] `git diff -- test_codex_team` (plus explicit inspection of untracked task files) shows no accidental contract rewrite or generated/vendor artifact.
- [ ] No path outside `test_codex_team/` is staged, modified, deleted, cleaned, or included in the proposed commit; especially preserve `frontend-prototype/` and `output/`.
- [ ] Any staged-file list is inspected explicitly before commit, and the eventual commit contains only reviewed task files.

## Developer completion definition

worker_A_dev may report **development complete** only when all of the following are true:

1. Orders 1-8 above are complete and every implementation/documentation file is under `test_codex_team/`.
2. All mandatory acceptance items are checked with reproducible evidence, or each exception is identified as a blocker/deviation requiring leader_A's decision; a live-backend blocker must not be presented as a passing integration check.
3. The exact Node test command exits 0, the static frontend starts on port `5173`, and browser console/module-load results are recorded.
4. Backend health and full list/add/toggle/delete smoke are recorded when the backend is available; otherwise the handoff clearly separates completed frontend verification from blocked cross-team verification.
5. `ruff check .` and `pytest` have been run and their results truthfully attributed; unrelated failures are reported, not repaired outside scope.
6. `DEVELOPMENT_LOG.md` contains environment versions, commands/results, changed files, deviations, blockers, residual risks, and scoped Git status.
7. No implementation commit is required from the developer unless leader_A explicitly requests it; no unrelated path is staged or modified.

## Independent reviewer input and required conclusion

After verifying the developer completion definition, leader_A should give worker_A_rev this complete input bundle:

- `SPEC.md`, this `PLAN.md`, and `DECISIONS.md` as the frozen requirements/contract baseline;
- every task-owned source, test, README, and `DEVELOPMENT_LOG.md` file under `test_codex_team/`;
- developer-reported exact commands/results, environment versions, smoke matrix, browser/backend availability, deviations, blockers, and residual risks;
- current `git status --short`, task-path diff/untracked-file inspection, and any staged-file list; and
- explicit instruction to rerun relevant checks, inspect behavior independently, avoid implementation fixes, and write `test_codex_team/project_records/REVIEW.md`.

`REVIEW.md` must enumerate evidence, findings with severity and precise file/behavior location, contract consistency, acceptance items sampled or rerun, accessibility/browser results, Git-scope result, and one unambiguous conclusion: **approve**, **reject**, or **blocked**. Any reject returns to leader_A for a scoped developer fix and a fresh independent review.

## PM gate recommendation

**PASS, verified by leader_A at 2026-08-10T10:19:04-07:00.** The confirmed contract is unchanged, the development package is decomposed with explicit dependencies and ownership, risks have observable triggers and mitigations, and acceptance/hand-off criteria are executable. This verification opens only the development gate; review remains prohibited until the developer handoff is accepted by leader_A.
