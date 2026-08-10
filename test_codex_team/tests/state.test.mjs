import assert from "node:assert/strict";
import test from "node:test";

import {
  appendTodo,
  normalizeTodos,
  removeTodo,
  replaceTodo,
  transitionReconciliationGate,
} from "../frontend/state.mjs";

const initial = [
  { id: "1", title: "First", completed: false },
  { id: "2", title: "Second", completed: true },
];

test("normalizeTodos validates, copies, and preserves order", () => {
  const normalized = normalizeTodos(initial);
  assert.deepEqual(normalized, initial);
  assert.notEqual(normalized, initial);
  assert.notEqual(normalized[0], initial[0]);
  assert.throws(() => normalizeTodos([{ ...initial[0] }, { ...initial[0] }]), /unique/);
  assert.throws(() => normalizeTodos([{ id: "1", title: "Bad", completed: 1 }]), /boolean/);
});

test("appendTodo adds exactly one item without mutating the source", () => {
  const next = appendTodo(initial, { id: "3", title: "Third", completed: false });
  assert.deepEqual(next.map(({ id }) => id), ["1", "2", "3"]);
  assert.deepEqual(initial.map(({ id }) => id), ["1", "2"]);
  assert.throws(() => appendTodo(initial, { ...initial[0] }), /already exists/);
});

test("replaceTodo changes only the matching item and preserves source state", () => {
  const updated = { id: "1", title: "First", completed: true };
  const next = replaceTodo(initial, updated);
  assert.deepEqual(next, [updated, initial[1]]);
  assert.deepEqual(initial[0], { id: "1", title: "First", completed: false });
  assert.throws(
    () => replaceTodo(initial, { id: "missing", title: "Missing", completed: false }),
    /does not exist/,
  );
});

test("removeTodo removes only the matching id and leaves input untouched", () => {
  const next = removeTodo(initial, "1");
  assert.deepEqual(next, [initial[1]]);
  assert.deepEqual(initial.map(({ id }) => id), ["1", "2"]);
  assert.deepEqual(removeTodo(initial, "missing"), initial);
});

test("reconciliation remains locked until a list load succeeds", () => {
  let locked = transitionReconciliationGate(false, "mutation-failed");
  assert.equal(locked, true);
  locked = transitionReconciliationGate(locked, "load-failed");
  assert.equal(locked, true);
  locked = transitionReconciliationGate(locked, "load-succeeded");
  assert.equal(locked, false);
  assert.throws(() => transitionReconciliationGate(false, "retry-clicked"), /Unknown/);
});
