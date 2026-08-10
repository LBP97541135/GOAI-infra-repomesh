# Todo List Frontend Independent Review

## Review stage record

- Review cycle: 1 (initial independent review)
- Gate opened: 2026-08-10T10:44:17-07:00
- Review completed: 2026-08-10T10:53:15-07:00
- Role: `worker_A_rev` (independent reviewer; not involved in implementation)
- Working directory: `D:\Project4work\GOAI-infra-repomesh`
- Inputs: `SPEC.md`, `PLAN.md` (including the PM checklist, risks, and completion definition), `DECISIONS.md` Decisions 006-008, `DEVELOPMENT_LOG.md`, every frontend/server/test/README file under `test_codex_team/`, root `AGENTS.md`, and current Git/staging state.
- Decision: **FAILED / REJECT**. Automated suites and bounded browser samples pass, but the review found one high-severity delivery-rule violation, three additional standards findings, and one medium spec mismatch. The independent-review gate must return to leader_A for scoped developer fixes; the reviewer did not modify implementation.
- Output: this `REVIEW.md`, precise findings, rerun evidence, sampled acceptance items, Git-scope evidence, and residual risks.
- Re-review status: not started. A fresh independent review is required after leader_A routes the findings to the developer.

## Review timeline

| Time (America/Los_Angeles) | Stage | Input and decision | Output / validation |
| --- | --- | --- | --- |
| 2026-08-10T10:44:17-07:00 | Gate authorization | leader_A supplied the frozen records, implementation, reported test/CRUD evidence, known repository-baseline failures, and Git scope. | Review cycle 1 opened; implementation edits prohibited. |
| 2026-08-10T10:44:17-07:00 to 10:48:00-07:00 | Full source and record review | Read all task-owned records, source, tests, server, and README; compared them with root `AGENTS.md`. | Contract, state, safety, accessibility, and documentation review completed independently of developer conclusions. |
| 2026-08-10T10:48:00-07:00 to 10:48:22-07:00 | Automated rerun | Ran the exact Node and server unittest commands. | Node: 21 passed, 0 failed. Server: 2 passed, 0 failed. |
| 2026-08-10T10:50:11-07:00 to 10:52:07-07:00 | Bounded Chromium sampling | Started only the task-owned frontend on previously free port `35273`; did not start, stop, or contact a B-owned process except for the expected failed browser request to the documented but unavailable `localhost:31080` override. | Verified module loading, network-error/retry UI, keyboard order, unsafe override rejection, no unsafe backend request, and 320x700 layout. Browser and port-35273 server were then closed. |
| 2026-08-10T10:53:15-07:00 | Final gate decision | Aggregated standards and spec axes plus Git scope. | **FAILED / REJECT**; precise findings below. |

No Playwright command remained stuck. The longest interaction batch yielded after 31.1 seconds and completed after one additional 3.2-second bounded wait. The reviewer-owned browser closed successfully, and a listener check returned `35273: no listener`.

## Commands and results

```powershell
node --test test_codex_team/tests/*.test.mjs
```

- Exit `0`; 21 tests passed, 0 failed, 0 skipped/cancelled.
- Covered exact API requests/status handling, response validation and error normalization, default/safe override behavior, immutable state helpers, and compatibility entry points.

```powershell
python -m unittest discover -s test_codex_team/tests -p "test_serve_frontend.py" -v
```

- Exit `0`; 2 tests passed.
- Actual HTTP checks returned `200 text/javascript` for `app.js` and `app.mjs`; encoded `/%2e%2e/README.md` returned `404`; default/override parsing returned `5173`/`35173`.

```powershell
python test_codex_team/serve_frontend.py --port 35273
```

- Port `35273` was checked free before start. The task server bound to loopback and served `index.html`, `styles.css`, `app.js`, `api.js`, and `state.js` successfully.
- At `http://localhost:35273/?apiBase=http%3A%2F%2Flocalhost%3A31080`, the unavailable backend produced the actionable alert and `Retry loading` control without showing a false empty state. The only console error was the expected refused backend request.
- Keyboard Tab order was New todo input -> Add todo -> Retry loading.
- At `?apiBase=https%3A%2F%2Fevil.example`, the UI visibly rejected the override, disabled Add, made no backend request, and reported 0 console errors/warnings.
- At 320x700, content remained readable with stacked form controls and no observed clipping.

```powershell
git status --short
git diff --cached --name-only
git diff --name-only -- test_codex_team
git ls-files --others --exclude-standard -- test_codex_team
git check-ignore -v test_codex_team/__pycache__/serve_frontend.cpython-311.pyc test_codex_team/tests/__pycache__/test_serve_frontend.cpython-311.pyc
```

- Status: only pre-existing `?? frontend-prototype/`, pre-existing `?? output/`, and task-owned `?? test_codex_team/`.
- Staged list: empty. Tracked task diff: empty because the task directory is untracked.
- All proposed non-ignored task files are under `test_codex_team/`.
- Two ignored Python bytecode files exist under task scope; see finding S4.

Repository-wide `ruff check .` and `pytest` were not repeated: the supplied, attributed baseline remains that `ruff` is unavailable and `pytest` exits during root `conftest.py` import with `ModuleNotFoundError: repomesh`. Per dispatch, this review did not cross scope to repair those failures.

## Standards axis

### S1 — HIGH — POST has neither an idempotency key nor a safe documented retry policy

- Rule: root `AGENTS.md:16`, "Every external side effect needs an idempotency key or a documented retry policy."
- Location: `frontend/api.js:110-136` and `frontend/api.js:153-165`; `frontend/app.js:118-146`; `README.md:73` and `README.md:79`.
- Evidence: create sends only `Accept` and `Content-Type`, with no idempotency token. A lost response after a successful server-side create is surfaced as a generic network failure; the UI re-enables Add and the README tells the user to retry the mutation. That retry can create a duplicate after an ambiguous outcome.
- Required disposition: define and implement a contract-compatible idempotency mechanism, or document and enforce a retry/reconciliation policy that handles ambiguous POST outcomes without instructing an unsafe blind retry.

### S2 — MEDIUM — item mutation rendering drops focus while the request is in flight

- Rule: `PLAN.md:143-144` requires keyboard-only operation and visible focus preservation.
- Location: `frontend/app.js:90-97`, `frontend/app.js:149-165`, and `frontend/app.js:169-185`.
- Evidence: toggle/delete add the item ID to the pending set and immediately call `render()`. `render()` uses `replaceChildren`, removing the focused checkbox/button. Focus is restored only in `finally`, after the request completes; during a slow request focus falls off the visible control, rather than being preserved.
- Required disposition: preserve a stable focused control during in-flight mutations and verify the behavior with a delayed browser response.

### S3 — LOW — one test asserts source structure instead of behavior

- Rule: root `AGENTS.md:14`, "Add tests for behavior, not directory structure."
- Location: `tests/entrypoints.test.mjs:24-27`.
- Evidence: the test reads `app.mjs` as text and requires one exact import string. It locks file structure but does not prove the browser entry point loads or behaves correctly.
- Required disposition: replace or supplement this assertion with a behavior-level module/server/browser check.

### S4 — LOW — ignored generated bytecode remains in the task directory

- Rule: `PLAN.md:159` requires explicit untracked inspection with no generated/vendor artifact.
- Location: `test_codex_team/__pycache__/serve_frontend.cpython-311.pyc` and `test_codex_team/tests/__pycache__/test_serve_frontend.cpython-311.pyc`.
- Evidence: both files are hidden from normal Git status by `.gitignore:1`, but recursive filesystem inspection finds them. This contradicts `DEVELOPMENT_LOG.md:95`, which records a remaining repository-local artifact count of zero.
- Required disposition: remove only these task-owned generated files before re-review and verify by filesystem enumeration, not only `git status` or `rg --files`.

No additional actionable Fowler smell was found. The thin `.mjs` forwarding entries are justified by Decision 007, and duplicate Todo validation is separated across API-boundary and pure-state responsibilities.

## Spec axis

### P1 — MEDIUM — the UI adds an unfrozen 200-character title limit

- Requirement: `SPEC.md:21` requires adding a todo with a non-empty title; `SPEC.md:35` defines the POST body as `{ "title": string }`; `PLAN.md:113` requires any title containing non-whitespace text to send one create request.
- Location: `frontend/index.html:23-31`, specifically `maxlength="200"` at line 28.
- Evidence: the browser prevents input beyond 200 characters even though the frozen contract defines no maximum. This is an undocumented client-side restriction and is not covered by tests.
- Required disposition: remove the unfrozen limit, or stop and obtain a written cross-team contract decision before imposing it.

No other frozen-route, method, status, JSON-shape, CORS/default-port, safe-override, MIME, or README-command mismatch was found.

## Acceptance sampling

| Area | Result | Independent evidence |
| --- | --- | --- |
| Default and safe `apiBase` | PASS | Static review plus Node tests; exact default is `http://localhost:8000`, override is loopback origin-only. |
| Unsafe `apiBase` | PASS | Browser rejected `https://evil.example`, disabled Add, emitted no backend request, and had 0 console errors/warnings. |
| GET/POST/PATCH/DELETE request contract | PASS (static/unit) | Exact paths, methods, JSON headers/bodies, expected 200/201/200/204, Todo validation, and 204 no-JSON behavior inspected and rerun. |
| Duplicate submit while POST is pending | PASS for immediate double action; FAIL for ambiguous retry safety | In-flight guard is present and existing developer/leader CRUD evidence records a single POST from double-click. Finding S1 covers response-loss retry ambiguity. |
| Last-known-good list on mutation failure | PASS (static) | State changes occur only after validated success; catch paths retain `todos` and re-enable controls. |
| Loading / empty / error | PASS | Browser independently verified initial failure replaces loading with actionable error and not false empty; static branches are deterministic. |
| Accessibility semantics and keyboard order | PARTIAL / FAIL | Labels, native controls, live regions, names, focus ring, and initial error-path Tab order pass; in-flight item mutation focus fails static review (S2). |
| Narrow viewport | PASS (sampled) | 320x700 visual sample remained readable with stacked form controls. |
| Frontend server default/override/MIME/boundary | PASS | Server unittest 2/2 plus static review of loopback binding/root. |
| Real cross-repository CRUD | ACCEPTED EXISTING EVIDENCE, NOT RERUN | `DEVELOPMENT_LOG.md:98-121` and `:123-135` record developer and leader_A independent GET 200 / POST 201 / PATCH 200 / DELETE 204 runs with console 0. Manager explicitly authorized using this evidence; reviewer did not touch B services. |
| README commands | PASS | Commands and Decision 006-008 match exactly. |
| Git scope / artifacts / staging | FAIL | No staging or out-of-scope task changes, but ignored task-owned bytecode exists (S4). |

## Residual risks and conclusion

- Existing browser CRUD evidence does not replace a re-review of the corrected focus and ambiguous-POST behavior with delayed/response-loss scenarios.
- Live-region behavior was sampled through Chromium accessibility output, not a full assistive-technology session.
- Repository-wide `ruff`/`pytest` remain an attributed non-task baseline risk and must be repaired by the owning scope before PR-level checks can become green.

**FINAL CONCLUSION: FAILED / REJECT.** Standards axis: 4 findings (worst HIGH, S1). Spec axis: 1 finding (worst MEDIUM, P1). leader_A should route S1-S4 and P1 to the developer, preserve unrelated untracked directories, and reopen a fresh independent review after scoped fixes and focused regression evidence.

---

# Independent Re-review Round 2

## Re-review stage record

- Review cycle: 2 (independent re-review after scoped repair)
- Gate opened: 2026-08-10T11:12:02-07:00
- Re-review completed: 2026-08-10T11:19:37-07:00
- Role: `worker_A_rev` (independent reviewer; did not implement the repair)
- Inputs: the unchanged round-1 FAILED history above; current `AGENTS.md`, `SPEC.md`, `PLAN.md`, Decisions 006-009, `DEVELOPMENT_LOG.md`, README, every current frontend/server/test file, round-1 findings S1-S4/P1, and current cache/Git/staging state.
- Decision: **APPROVED**. S1-S4 and P1 are independently closed; no new blocking or residual finding was accepted.
- Output: this appended round-2 stage record, exact rerun/browser evidence, per-finding status, residual-risk statement, Git scope, and final gate conclusion.
- Validation boundary: browser re-review covered only S1/S2/P1 using the reviewer-owned frontend port `35573` and Playwright routes on that same origin. No external or B-owned service was started, stopped, or contacted.

## Round-2 timeline

| Time (America/Los_Angeles) | Stage | Input / decision | Output / validation |
| --- | --- | --- | --- |
| 2026-08-10T11:12:02-07:00 | Gate authorization | leader_A accepted the scoped repair handoff and opened round 2 without changing the round-1 conclusion. | Re-review started; implementation edits remained prohibited. |
| 2026-08-10T11:12:02-07:00 to 11:14:08-07:00 | Static review and automated rerun | Read current implementation, tests, README, development evidence, and Decision 009; ran the required commands. | Node 25/25 passed; Python `-B` server tests 2/2 passed; cache scans before and after were empty. |
| 2026-08-10T11:15:12-07:00 to 11:18:53-07:00 | Bounded Chromium re-review | Used loopback frontend port `35573` plus same-origin route simulations for delayed mutations, response loss, failed/successful reconciliation, and a 250-character title. | S1/S2/P1 browser behaviors passed. Three setup attempts failed immediately due CLI command quoting/length and were replaced by shorter bounded scenarios; none hung or changed repository files. |
| 2026-08-10T11:19:37-07:00 | Cleanup, scope, and conclusion | Closed reviewer Chromium and stopped only reviewer port `35573`; rescanned cache, Git, and staging. | `35573: no listener`; no cache, staging, commit, or unexpected scope; **APPROVED**. |

## Exact automated verification

```powershell
node --test test_codex_team/tests/*.test.mjs
```

- Exit `0`; 25 tests passed, 0 failed, 0 skipped/cancelled.
- New behavior coverage independently passed for one-attempt ambiguous create/delete, uncertain malformed create success, reconciliation-gate persistence, behavioral `.js`/`.mjs` entries, and an unchanged 250-character title body.

```powershell
python -B -m unittest discover -s test_codex_team/tests -p "test_serve_frontend.py" -v
```

- Exit `0`; 2 tests passed.
- Actual `app.js` and `app.mjs` responses were `200 text/javascript`; encoded parent access returned `404`; default and override ports remained `5173` and `35173`.
- Recursive scans immediately before the Node run, after the Python `-B` run, and after browser cleanup all found no `__pycache__` directory or `.pyc` file under `test_codex_team/`.

## Finding disposition

| Finding | Round-2 status | Independent closure evidence |
| --- | --- | --- |
| S1 HIGH — unsafe ambiguous mutation retry | **CLOSED** | `api.js:81-100,117-147` performs one request and provides non-blind mutation guidance. `app.js:85-99,154-181,184-266` sends every mutation once, enters the global gate on any mutation failure, preserves the gate on failed GET, and unlocks only after validated successful GET. `README.md:73-89` matches the UI/API policy and explicitly prohibits POST resubmission. Node tests cover single-attempt POST/DELETE and gate transitions. Browser response-loss simulation observed one 250-character POST, Add disabled, visible `Reload todos to reconcile`, and `Do not submit the title again`; an Enter attempt left the POST count at one. The first reconciliation GET was aborted and the gate remained locked; the second succeeded and unlocked it. |
| S2 MEDIUM — focus lost during item mutation | **CLOSED** | `app.js:74-83,219-266` changes pending semantics in place before `await`, with `aria-busy`, `aria-disabled`, and event guards; it does not replace the list until settlement. During a 900 ms PATCH the original checkbox returned `active=true`, `connected=true`, `aria-disabled=true`, item `aria-busy=true`; after success the replacement checkbox was checked and focused. During a 900 ms DELETE the original button returned the same four pending/focus properties; after success the item count was zero and focus was `#todo-title`. |
| S3 LOW — structural entrypoint assertion | **CLOSED** | `tests/entrypoints.test.mjs:9-30` now executes validated list behavior and immutable state-transition behavior through both `.js` and `.mjs` exports; it no longer reads source text or asserts an exact import string. Node rerun passed both tests. |
| S4 LOW — generated Python bytecode | **CLOSED** | README's canonical command is `python -B` at `README.md:41-44`. Recursive filesystem scans found no task-owned cache before the rerun, after the rerun, or at final scope check; the canonical server test did not recreate bytecode. |
| P1 MEDIUM — unfrozen 200-character title limit | **CLOSED** | `index.html:23-30` has no `maxlength`; `app.js:192-204` and `api.js:164-175` do not truncate a non-empty title; `api.test.mjs:206-218` sends 250 characters unchanged. Browser routing independently captured a POST body length of 250 and, after successful reconciliation, rendered a title length of 250. |

## Browser command limits and results

- The first combined route command failed immediately after 6.2 seconds with Windows `The command line is too long`; no page scenario ran.
- The first split S2 command failed immediately after 5.3 seconds with CLI `SyntaxError: Unexpected token '-'`; no page scenario ran.
- One S1/P1 retry command failed immediately after 6.9 seconds with CLI `too many arguments`; no page scenario ran.
- Shorter same-origin route scenarios then completed successfully in 9.3, 7.2, and 6.3 seconds. No command hung or required an unbounded wait.
- Expected console network errors came only from the intentionally aborted POST and reconciliation GET routes. No unexpected module/application error was observed.
- Reviewer browser `worker-a-rev-r2` closed successfully. The reviewer-owned port `35573` was stopped and independently confirmed to have no listener.

## Standards axis, spec axis, and new-finding review

- Standards axis: S1-S4 closed; 0 open findings. No new documented-standard breach or actionable Fowler smell was found.
- Spec axis: P1 closed; 0 open findings. Frozen routes, methods, statuses, payloads, default/override behavior, MIME, directory boundary, and run commands remain unchanged.
- A candidate concern was examined because the native checkbox reports `checked=true` while its slow PATCH is pending. This is not classified as a finding: the last valid `todos` model is unchanged until validated success, the item exposes busy/disabled semantics, and the failure path re-renders the unchanged model as an explicit rollback. That matches `PLAN.md:98`, which permits either commit-after-success or explicit rollback.
- `PLAN.md:134-135` retains older wording about retrying affected controls, but Decision 009 and the explicit round-2 requirements supersede it with the stricter global reconciliation gate. The implementation, README, API guidance, and tests consistently follow the later approved decision; this is not treated as a contract defect.

## Final cache and Git scope

```powershell
git status --short
git diff --cached --name-only
git diff --name-only -- test_codex_team
git ls-files --others --exclude-standard -- test_codex_team
```

- Status remains only pre-existing `?? frontend-prototype/`, pre-existing `?? output/`, and task-owned `?? test_codex_team/`.
- Staged list and tracked task diff are empty. No commit exists for the task-owned directory.
- Every proposed task file, including this appended review record, is under `test_codex_team/`; no generated/cache/vendor artifact was found.

## Residual risks and final round-2 conclusion

- Repository-wide `ruff` remains unavailable and root `pytest` remains blocked by the attributed pre-task `ModuleNotFoundError: repomesh`; this re-review did not broaden scope to repair either baseline.
- The browser checks used deterministic route simulation rather than a B-owned backend, by explicit re-review scope. Existing developer and leader_A real CRUD evidence remains unchanged.
- A full assistive-technology session was not repeated; Chromium focus/DOM/ARIA properties were checked directly for the repaired paths.

**ROUND-2 FINAL CONCLUSION: APPROVED.** Standards axis: 0 open findings. Spec axis: 0 open findings. S1-S4 and P1 are closed with independent static, automated, browser, cache, and Git evidence. The round-1 FAILED / REJECT conclusion remains preserved above as historical record.
