# Todo List frontend

This directory contains the dependency-free browser frontend for the frozen Todo List HTTP contract. It uses plain HTML, CSS, browser ES modules, and Node's built-in test runner; no `npm install` step is required.

## Start the frontend

From the repository root:

```powershell
python test_codex_team/serve_frontend.py
```

Open [http://localhost:5173](http://localhost:5173) or [http://127.0.0.1:5173](http://127.0.0.1:5173). The frontend calls `http://localhost:8000` by default.

For safe local integration testing on alternate ports, pass a loopback HTTP(S) origin in the `apiBase` query parameter:

```text
http://localhost:35173/?apiBase=http%3A%2F%2Flocalhost%3A31080
```

The override accepts only `localhost`, `127.0.0.1`, or `[::1]`, with no credentials, path, query, or fragment. Invalid values are rejected visibly; the default never changes implicitly.

The alternate-port static command used only for integration testing is:

```powershell
python test_codex_team/serve_frontend.py --port 35173
```

The matching test backend is expected at `http://localhost:31080`. From its repository's `test_codex_team/`, its exact smoke command is:

```powershell
python backend/server.py --port 31080 --allow-origin http://localhost:35173
```

The task-owned frontend server binds only to loopback, serves only `test_codex_team/frontend`, and explicitly returns `text/javascript` for both `.js` and `.mjs` modules.

## Run automated tests

From the repository root:

```powershell
node --test test_codex_team/tests/*.test.mjs
python -B -m unittest discover -s test_codex_team/tests -p "test_serve_frontend.py" -v
```

The tests use only Node, browser, and Python standard-library facilities. API tests make no live network requests; the server test uses an ephemeral loopback listener.

## Backend dependency

The backend must listen at `http://localhost:8000` and permit CORS from both supported frontend origins. In the backend repository's `test_codex_team/` directory, its documented commands are:

```powershell
python backend/server.py
python -m unittest discover -s backend/tests -v
```

Confirm readiness before an end-to-end smoke:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

The expected result is `{ "status": "ok" }`.

## End-to-end smoke

1. Start the backend and confirm `/health` returns status 200 with `{ "status": "ok" }`.
2. Start this static frontend on port 5173.
3. Open `http://localhost:5173`, then repeat the smoke at `http://127.0.0.1:5173`.
4. Verify the loading message appears, followed by either the returned list or the explicit empty state.
5. Add a non-empty todo. Rapidly submit twice while the first request is pending and confirm only one create request is accepted.
6. Toggle the todo complete and incomplete, then delete it. Confirm unrelated todos remain unchanged.
7. Simulate a lost mutation response. Confirm the page keeps the last known list, locks all mutations, and shows **Reload todos to reconcile**. A failed reload must keep the gate locked; only a successful list GET may unlock mutations.
8. During a delayed toggle or delete, confirm the focused item control remains the active DOM node while the item reports busy/disabled semantics and rejects duplicate actions.
9. Use the keyboard only to reach the input, add button, checkboxes, delete buttons, and reload button. Confirm visible focus and meaningful accessible names.
10. Check the browser console and Network panel for module-load, request-shape, CORS, and mixed-origin errors.

## Mutation retry and reconciliation policy

- The frontend never automatically retries `POST`, `PATCH`, or `DELETE`.
- A failed `POST` may have created the todo even when its response was lost. The app therefore locks Add and every other mutation, tells the user not to resubmit, and requires **Reload todos to reconcile**. A failed list reload preserves the lock; only a successful `GET /api/todos` clears it.
- `PATCH` sends an absolute `{ "completed": boolean }`, so repeating the same value is semantically idempotent. The UI still does not guess or blindly retry after an uncertain response; it requires list reconciliation first.
- `DELETE` is not blindly repeated after an uncertain response. The UI requires a list reload to discover whether the todo still exists.
- Read-only list GET failures may be retried with **Retry loading** because they do not create external side effects.

## Troubleshooting

- **Could not reach the backend while loading:** start the backend, verify port 8000, then use **Retry loading**.
- **Mutation outcome uncertain:** do not repeat the mutation. Use **Reload todos to reconcile**; if reload fails, the mutation lock intentionally remains active.
- **CORS failure:** serve this frontend on port 5173 and use exactly `localhost` or `127.0.0.1`. Confirm the backend allows both origins, `GET`/`POST`/`PATCH`/`DELETE`, preflight `OPTIONS`, and `Content-Type`.
- **Frontend files return 404:** run `python test_codex_team/serve_frontend.py` from the repository root. The server always confines requests to the task frontend directory.
- **Override is rejected:** URL-encode an origin-only loopback `http://` or `https://` URL in the `apiBase` parameter. Do not include credentials, a path, a query, or a fragment.
