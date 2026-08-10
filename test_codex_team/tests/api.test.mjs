import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_BACKEND_URL,
  TodoApiError,
  createTodoApi,
  resolveBackendBaseUrl,
  validateTodo,
} from "../frontend/api.mjs";

function jsonResponse(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

test("default and safe apiBase query-parameter backend URLs are deterministic", () => {
  assert.equal(resolveBackendBaseUrl(), DEFAULT_BACKEND_URL);
  assert.equal(
    resolveBackendBaseUrl("?apiBase=http%3A%2F%2Flocalhost%3A31080"),
    "http://localhost:31080",
  );
  assert.equal(resolveBackendBaseUrl("?unrelated=value"), DEFAULT_BACKEND_URL);
});

test("apiBase rejects unsafe or non-origin overrides", () => {
  for (const value of [
    "file:///tmp",
    "https://example.com",
    "http://user:password@localhost:31080",
    "http://localhost:31080/api",
    "http://localhost:31080/?token=secret",
    "http://localhost:31080/#fragment",
  ]) {
    assert.throws(
      () => resolveBackendBaseUrl(`?apiBase=${encodeURIComponent(value)}`),
      TodoApiError,
      value,
    );
  }
});

test("listTodos constructs GET and validates every todo", async () => {
  const calls = [];
  const api = createTodoApi({
    fetchImpl: async (...args) => {
      calls.push(args);
      return jsonResponse({ todos: [{ id: "1", title: "Read", completed: false }] }, 200);
    },
  });

  assert.deepEqual(await api.listTodos(), [{ id: "1", title: "Read", completed: false }]);
  assert.equal(calls[0][0], "http://localhost:8000/api/todos");
  assert.deepEqual(calls[0][1], {
    method: "GET",
    headers: { Accept: "application/json" },
  });
});

test("addTodo sends the exact JSON mutation and accepts only 201", async () => {
  const calls = [];
  const api = createTodoApi({
    baseUrl: "http://localhost:8000/",
    fetchImpl: async (...args) => {
      calls.push(args);
      return jsonResponse({ id: "2", title: "Ship", completed: false }, 201);
    },
  });

  assert.deepEqual(await api.addTodo("Ship"), { id: "2", title: "Ship", completed: false });
  assert.deepEqual(calls[0], [
    "http://localhost:8000/api/todos",
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ title: "Ship" }),
    },
  ]);
});

test("toggleTodo encodes the id and sends the inverse state supplied by the caller", async () => {
  const calls = [];
  const api = createTodoApi({
    fetchImpl: async (...args) => {
      calls.push(args);
      return jsonResponse({ id: "a/b", title: "Test", completed: true }, 200);
    },
  });

  await api.toggleTodo("a/b", true);
  assert.deepEqual(calls[0], [
    "http://localhost:8000/api/todos/a%2Fb",
    {
      method: "PATCH",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ completed: true }),
    },
  ]);
});

test("deleteTodo accepts 204 without parsing a response body", async () => {
  let jsonWasRead = false;
  const api = createTodoApi({
    fetchImpl: async () => ({
      status: 204,
      json() {
        jsonWasRead = true;
        throw new Error("must not run");
      },
    }),
  });

  assert.equal(await api.deleteTodo("7"), undefined);
  assert.equal(jsonWasRead, false);
});

test("server detail is surfaced without blind mutation retry guidance", async () => {
  const api = createTodoApi({
    fetchImpl: async () => jsonResponse({ detail: "Todo not found" }, 404),
  });

  await assert.rejects(api.toggleTodo("missing", true), (error) => {
    assert.equal(error.status, 404);
    assert.match(error.message, /Todo not found/);
    assert.match(error.message, /Do not retry the mutation blindly/);
    assert.doesNotMatch(error.message, /Try the operation again/);
    return true;
  });
});

test("network, invalid JSON, and invalid success payload failures are actionable", async (t) => {
  await t.test("network", async () => {
    const api = createTodoApi({ fetchImpl: async () => { throw new Error("offline"); } });
    await assert.rejects(api.listTodos(), /Could not reach the backend.*reload the todo list/);
  });

  await t.test("invalid JSON", async () => {
    const api = createTodoApi({ fetchImpl: async () => new Response("not-json", { status: 200 }) });
    await assert.rejects(api.listTodos(), /invalid JSON/);
  });

  await t.test("invalid list envelope", async () => {
    const api = createTodoApi({ fetchImpl: async () => jsonResponse({ items: [] }, 200) });
    await assert.rejects(api.listTodos(), /"todos" array/);
  });

  await t.test("invalid Todo field", async () => {
    const api = createTodoApi({
      fetchImpl: async () => jsonResponse({ todos: [{ id: 1, title: "bad", completed: false }] }, 200),
    });
    await assert.rejects(api.listTodos(), /id must be a string/);
  });
});

test("an ambiguous create failure is marked uncertain and forbids blind retry", async () => {
  let attempts = 0;
  const api = createTodoApi({
    fetchImpl: async () => {
      attempts += 1;
      throw new Error("response lost");
    },
  });

  await assert.rejects(api.addTodo("May already exist"), (error) => {
    assert.equal(error.outcomeUncertain, true);
    assert.match(error.message, /outcome is uncertain/);
    assert.match(error.message, /Do not retry it blindly/);
    assert.match(error.message, /reload todos to reconcile/);
    return true;
  });
  assert.equal(attempts, 1);
});

test("an ambiguous delete is attempted once and requires reconciliation", async () => {
  let attempts = 0;
  const api = createTodoApi({
    fetchImpl: async () => {
      attempts += 1;
      throw new Error("response lost");
    },
  });

  await assert.rejects(api.deleteTodo("uncertain"), (error) => {
    assert.equal(error.outcomeUncertain, true);
    assert.match(error.message, /Do not retry it blindly/);
    assert.match(error.message, /reload todos to reconcile/);
    return true;
  });
  assert.equal(attempts, 1);
});

test("a malformed successful create response remains an uncertain outcome", async () => {
  const api = createTodoApi({
    fetchImpl: async () => new Response("not-json", { status: 201 }),
  });

  await assert.rejects(api.addTodo("Created somewhere"), (error) => {
    assert.equal(error.outcomeUncertain, true);
    assert.match(error.message, /invalid JSON/);
    return true;
  });
});

test("titles longer than 200 characters are sent unchanged", async () => {
  const title = "x".repeat(250);
  let requestBody;
  const api = createTodoApi({
    fetchImpl: async (_url, options) => {
      requestBody = JSON.parse(options.body);
      return jsonResponse({ id: "long", title, completed: false }, 201);
    },
  });

  assert.equal((await api.addTodo(title)).title.length, 250);
  assert.deepEqual(requestBody, { title });
});

test("client-side argument validation prevents malformed mutation requests", async () => {
  let callCount = 0;
  const api = createTodoApi({
    fetchImpl: async () => {
      callCount += 1;
      return jsonResponse({}, 500);
    },
  });

  await assert.rejects(api.addTodo("   "), /must not be empty/);
  await assert.rejects(api.toggleTodo("1", "yes"), /must be a boolean/);
  await assert.rejects(api.deleteTodo(""), /must not be empty/);
  assert.equal(callCount, 0);
});

test("validateTodo returns a safe contract-only copy", () => {
  const input = { id: "1", title: "One", completed: false, extra: "ignored" };
  const result = validateTodo(input);
  assert.deepEqual(result, { id: "1", title: "One", completed: false });
  assert.notEqual(result, input);
});
