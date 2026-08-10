import assert from "node:assert/strict";
import test from "node:test";

import * as apiFromJs from "../frontend/api.js";
import * as apiFromMjs from "../frontend/api.mjs";
import * as stateFromJs from "../frontend/state.js";
import * as stateFromMjs from "../frontend/state.mjs";

test("both API entry points execute the same validated list behavior", async () => {
  for (const module of [apiFromJs, apiFromMjs]) {
    const api = module.createTodoApi({
      fetchImpl: async () => new Response(
        JSON.stringify({ todos: [{ id: "entry", title: "Loaded", completed: false }] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    });
    assert.deepEqual(await api.listTodos(), [
      { id: "entry", title: "Loaded", completed: false },
    ]);
  }
});

test("both state entry points execute the same immutable transition behavior", () => {
  const original = [{ id: "1", title: "One", completed: false }];
  const added = { id: "2", title: "Two", completed: false };
  for (const module of [stateFromJs, stateFromMjs]) {
    assert.deepEqual(module.appendTodo(original, added), [...original, added]);
    assert.deepEqual(original, [{ id: "1", title: "One", completed: false }]);
  }
});
