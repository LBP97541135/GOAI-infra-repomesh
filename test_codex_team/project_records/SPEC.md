# Todo List Frontend Specification

## Stage record

- Time: 2026-08-10T10:15:42-07:00
- Role: leader_A (frontend owner)
- Input: manager's cross-repository Todo List assignment and repository `AGENTS.md`
- Decision: use a dependency-free browser frontend under `test_codex_team/frontend/`, with the backend accessed only through the agreed HTTP contract
- Output: this frontend specification; the proposed API details were confirmed unchanged by leader_B at 2026-08-10T10:16:37-07:00
- Validation: checked the repository worktree before authoring; existing untracked `frontend-prototype/` and `output/` are outside this task and must remain untouched

## Goal

Deliver a small, actually runnable Todo List frontend that interoperates with the backend in leader_B's repository. New task content must remain under this repository's `test_codex_team/` directory.

## User-visible behavior

The page must:

1. Load and display the current todos.
2. Add a todo with a non-empty title.
3. Toggle a todo's `completed` state.
4. Delete a todo.
5. Show an explicit loading state during initial fetch.
6. Show an explicit empty-list state when no todos exist.
7. Show an actionable error message when an API operation fails.
8. Prevent duplicate submissions while an add request is in flight.

## Confirmed HTTP contract

- Frontend origin: `http://localhost:5173` (also usable as `http://127.0.0.1:5173`).
- Backend base URL: `http://localhost:8000`.
- `GET /health` returns status `200` and `{ "status": "ok" }`.
- `GET /api/todos` returns status `200` and `{ "todos": [Todo, ...] }`.
- `POST /api/todos` accepts `{ "title": string }` and returns status `201` with the created `Todo`.
- `PATCH /api/todos/{id}` accepts `{ "completed": boolean }` and returns status `200` with the updated `Todo`.
- `DELETE /api/todos/{id}` returns status `204` with no response body.
- `Todo` is `{ "id": string, "title": string, "completed": boolean }`.
- Errors use `{ "detail": string }`, with `400` for invalid input, `404` for a missing todo, and `500` for unexpected server errors.
- Backend CORS permits both frontend origins above, methods `GET`, `POST`, `PATCH`, `DELETE`, and the `Content-Type` request header.
- Backend implementation/run command from its repository `test_codex_team/`: `python backend/server.py`.
- Backend tests from its repository `test_codex_team/`: `python -m unittest discover -s backend/tests -v`.

## Frontend architecture

- Plain semantic HTML and CSS.
- Browser-native ES modules.
- A small API client owns HTTP and response validation.
- Pure todo helpers/state transitions are separated so Node's built-in test runner can test behavior without browser dependencies.
- Runtime backend URL defaults to `http://localhost:8000` and may be overridden by a documented query parameter or configuration hook if implementation needs it.

## Quality constraints

- No framework or package-install step unless the developer records a justified deviation.
- Accessible labels and native controls; keyboard operation must work.
- API failures must preserve the last known good list when possible.
- All external calls must be safe against accidental duplicate UI actions; mutating controls are disabled for the affected operation.
- Automated tests must cover request construction/response handling and core todo state behavior.
- README must include frontend start, test, backend dependency, and end-to-end smoke instructions.

## Acceptance criteria

- The frontend can list, add, toggle, and delete todos against the agreed backend.
- Loading, empty, and error feedback are visible and deterministic.
- The frontend automated test command exits successfully.
- The frontend is served locally on port `5173` and calls backend port `8000` without CORS errors.
- The documented JSON shapes and status handling match the backend implementation.
- All task-owned files are under `test_codex_team/` and no unrelated user changes are overwritten or cleaned.

## Non-goals

- Authentication, persistence guarantees, multi-user isolation, pagination, editing todo titles, or production deployment.
- Treating another runtime or message plane as a source of truth.
