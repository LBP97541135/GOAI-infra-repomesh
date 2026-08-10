# Todo List Cross-Team Decisions

## Decision 001: Shared runtime and API contract

- Time: 2026-08-10T10:16:37-07:00
- Roles: leader_A (frontend) and leader_B (backend)
- Input: leader_A's written proposal covering API routes, JSON, ports, CORS, run commands, and joint acceptance
- Decision: leader_B accepted every proposed item without modification
- Output:
  - Frontend is served from `test_codex_team/frontend/` at `http://localhost:5173` (or the `127.0.0.1` equivalent).
  - Backend listens at `http://localhost:8000`.
  - Backend allows CORS origins `http://localhost:5173` and `http://127.0.0.1:5173`, the required methods and `Content-Type`, including `OPTIONS` preflight.
  - `GET /health` -> `200 {"status":"ok"}`.
  - `GET /api/todos` -> `200 {"todos":[Todo,...]}`.
  - `POST /api/todos` with `{"title": string}` -> `201 Todo`.
  - `PATCH /api/todos/{id}` with `{"completed": boolean}` -> `200 Todo`.
  - `DELETE /api/todos/{id}` -> `204` with an empty body.
  - `Todo` is `{"id": string, "title": string, "completed": boolean}`.
  - Errors are `{"detail": string}`, using `400`, `404`, or `500` as appropriate.
  - Backend uses the Python standard library and in-memory storage.
  - Backend start command from its `test_codex_team/`: `python backend/server.py`.
  - Backend test command from its `test_codex_team/`: `python -m unittest discover -s backend/tests -v`.
- Validation: direct thread response from leader_B explicitly said “确认”, restated the data types/status behavior, and reported that the same contract was written into the backend repository records

## Decision 002: Frontend dependency strategy

- Time: 2026-08-10T10:16:37-07:00
- Role: leader_A
- Input: requirement to prefer a lightweight, low-dependency solution and the confirmed standard-library backend
- Decision: use plain HTML/CSS/browser JavaScript plus Node's built-in test runner; no package install or frontend framework
- Output: PM and developer must plan within that constraint unless they report a blocking compatibility issue before implementation
- Validation: Node availability and the exact test suite remain development-stage checks; no claim of successful execution is made here

## Decision 003: Workflow gate status

- Time: 2026-08-10T10:16:37-07:00
- Roles: leader_A and leader_B
- Input: manager's sequential workflow requirement
- Decision: both teams may enter PM only after the contract confirmation; development is still prohibited until PM completion
- Output: leader_A will now dispatch the complete leader-authored records only to worker_A_pm
- Validation: neither frontend developer nor reviewer received an implementation assignment before this record

## Decision 004: PM gate acceptance

- Time: 2026-08-10T10:19:04-07:00
- Roles: worker_A_pm (author) and leader_A (gate owner)
- Input: PM stage record, Orders 0-9 file-level breakdown, 10-item risk register, 35-item acceptance checklist, developer completion definition, and reviewer input bundle in `PLAN.md`
- Decision: leader_A accepts the PM output and opens the development gate; the independent-review gate remains closed
- Output: worker_A_dev may implement only the frozen frontend scope under `test_codex_team/` and must produce `DEVELOPMENT_LOG.md` with actual evidence
- Validation: leader_A read the complete updated `PLAN.md`, confirmed the HTTP contract was unchanged, and checked `git status --short`; unrelated `frontend-prototype/` and `output/` remain untracked and outside scope

## Decision 005: Exact frontend run contract

- Time: 2026-08-10T10:22:11-07:00
- Roles: leader_A, responding to leader_B's development-stage runbook clarification
- Input: the frozen frontend directory/port/API base and leader_B's need for copy-paste-compatible joint README instructions
- Decision: from the frontend repository root, start with `python test_codex_team/serve_frontend.py`; run tests with `node --test test_codex_team/tests/*.test.mjs`; no install or build command is required. This command was revised by Decision 007 after real Chromium MIME failures with the generic server.
- Output: prerequisites are Python 3 and a modern browser, plus Node.js 18+ for frontend tests; joint startup runs backend first, confirms `http://localhost:8000/health`, then starts the frontend and opens `http://localhost:5173`
- Validation: exact working directory, commands, prerequisites, API base, and startup order were sent directly to leader_B for its README and developer handoff; this clarification does not alter the HTTP contract

## Decision 006: Safe cross-repository smoke overrides

- Time: 2026-08-10T10:26:02-07:00
- Roles: manager (additional acceptance requirement), leader_A, and leader_B
- Input: externally owned processes already listening on default ports `5173` and `8000`, plus the manager requirement to complete a real cross-repository CRUD smoke without terminating or modifying them
- Decision: preserve all defaults and add one backward-compatible test combination: frontend port `35173`, backend port `31080`, and frontend query parameter `apiBase=http://localhost:31080`; prior candidate `15173/18081` is explicitly discarded
- Output:
  - Backend, from its `test_codex_team/`: `python backend/server.py --port 31080 --allow-origin http://localhost:35173`.
  - Backend `--allow-origin` is repeatable and appends to (never replaces) the default two port-5173 origins.
  - Frontend, from this repository root: `python test_codex_team/serve_frontend.py --port 35173` (revised by Decision 007).
  - Smoke URL: `http://localhost:35173/?apiBase=http%3A%2F%2Flocalhost%3A31080`.
  - Without overrides, backend remains `localhost:8000`, frontend documentation remains port `5173`, and frontend API base remains `http://localhost:8000`.
- Validation: both leaders performed read-only listener checks showing `35173` and `31080` free, then directly and twice confirmed the unique command/URL combination; the future smoke must exercise real list/create/toggle/delete and be recorded in development/review evidence

## Decision 007: Cross-platform frontend MIME server

- Time: 2026-08-10T10:31:11-07:00
- Roles: worker_A_dev (evidence), leader_A (decision), and leader_B (runbook confirmation)
- Input: two real Chromium attempts showing that this Windows/Anaconda Python 3.11 registry maps both `.mjs` and `.js` to `text/plain`; the browser rejected both as module scripts and the UI remained in Loading
- Decision: add a task-owned Python standard-library static server at `test_codex_team/serve_frontend.py` that serves only `test_codex_team/frontend`, maps `.js` and `.mjs` to `text/javascript`, and accepts `--port` with default `5173`
- Output: final default command is `python test_codex_team/serve_frontend.py`; final safe smoke command is `python test_codex_team/serve_frontend.py --port 35173`; all API, backend, CORS, and default port contracts otherwise remain unchanged
- Validation: leader_B accepted this final runbook revision and paused its review conclusion until B-side README/decisions match; worker_A_dev must add automated server/MIME/boundary coverage and repeat Chromium verification before development handoff

## Decision 008: Development gate acceptance

- Time: 2026-08-10T10:44:17-07:00
- Roles: worker_A_dev (implementation/handoff) and leader_A (gate owner)
- Input: complete task-owned source/tests/README, `DEVELOPMENT_LOG.md`, developer's test report, and the live backend held by leader_B on port `31080`
- Decision: accept the development handoff and open the independent-review gate; no commit is allowed yet
- Output: worker_A_rev receives the frozen records, complete implementation, exact commands/results, baseline failures, MIME decision history, and current Git scope
- Validation: leader_A read the source/runbook/log, independently reran Node tests (21 passed) and frontend-server tests (2 passed), and independently completed browser GET `200`, POST `201`, PATCH `200`, DELETE `204`; POST/PATCH carried `Access-Control-Allow-Origin: http://localhost:35173`, and the browser console reported 0 errors/0 warnings

## Decision 009: First-review repair gate

- Time: 2026-08-10T11:12:02-07:00
- Roles: worker_A_rev (round-1 findings), worker_A_dev (repair), and leader_A (repair gate owner)
- Input: round-1 `FAILED / REJECT` findings S1-S4/P1 in `REVIEW.md`, the developer's scoped repair and `DEVELOPMENT_LOG.md` evidence
- Decision: leader_A accepts the repair handoff for independent re-review; round-1 remains failed and no finding is considered closed until worker_A_rev independently verifies it
- Output: re-review scope is the mutation reconciliation policy, focus preservation, behavioral entrypoint tests, cache-free `-B` test command, removal of the 200-character limit, and regression checks
- Validation: leader_A independently reran Node tests (25 passed) and Python `-B` server tests (2 passed), found no `__pycache__`/`.pyc`, no `maxlength`, no staged changes, and confirmed task scope remained limited to `test_codex_team/`

## Decision 010: Final review and cross-repository consistency gate

- Time: 2026-08-10T11:23:49-07:00
- Roles: worker_A_rev, leader_A, and leader_B
- Input: round-2 `APPROVED` review, leader_A's final 25+2 test rerun and baseline checks, the completed real CRUD evidence, and leader_B's final comparison against its committed backend records
- Decision: all review findings are closed, frontend/backend interface and runbooks are final-consistent, and leader_A may commit only the reviewed `test_codex_team/` scope
- Output: final shared contract remains the five endpoints and JSON/status rules in Decision 001; default frontend/backend ports remain `5173/8000`; safe smoke remains `35173/31080`; reconciliation is frontend-only and adds no HTTP header or backend requirement
- Validation:
  - worker_A_rev preserved round-1 `FAILED / REJECT`, independently reran Node 25/25 and Python `-B` 2/2, closed S1-S4/P1, and issued round-2 `APPROVED` with zero new findings.
  - leader_A reran Node 25/25 and Python `-B` 2/2 immediately before this gate; `ruff` remained unavailable, while root `pytest` again exited at `tests/conftest.py` with the attributed `ModuleNotFoundError: repomesh`.
  - leader_B explicitly replied “最终一致” for routes, JSON/statuses, CORS, commands, override URL, and reconciliation boundary; its backend commit is `9e332a4a5ec7b747253cb567daa05c4fd6e18711` (`feat: add runnable todo backend`) with no task-related uncommitted content.
