// Canonical browser module; state.mjs re-exports this file for Node/test compatibility.
function cloneTodo(todo, label = "Todo") {
  if (todo === null || typeof todo !== "object" || Array.isArray(todo)) {
    throw new TypeError(`${label} must be an object.`);
  }
  if (typeof todo.id !== "string") {
    throw new TypeError(`${label}.id must be a string.`);
  }
  if (typeof todo.title !== "string") {
    throw new TypeError(`${label}.title must be a string.`);
  }
  if (typeof todo.completed !== "boolean") {
    throw new TypeError(`${label}.completed must be a boolean.`);
  }
  return { id: todo.id, title: todo.title, completed: todo.completed };
}

export function normalizeTodos(todos) {
  if (!Array.isArray(todos)) {
    throw new TypeError("Todos must be an array.");
  }
  const normalized = todos.map((todo, index) => cloneTodo(todo, `todos[${index}]`));
  const ids = new Set(normalized.map((todo) => todo.id));
  if (ids.size !== normalized.length) {
    throw new TypeError("Todo ids must be unique.");
  }
  return normalized;
}

export function appendTodo(todos, todo) {
  const current = normalizeTodos(todos);
  const next = cloneTodo(todo);
  if (current.some(({ id }) => id === next.id)) {
    throw new TypeError(`Todo id "${next.id}" already exists.`);
  }
  return [...current, next];
}

export function replaceTodo(todos, todo) {
  const current = normalizeTodos(todos);
  const next = cloneTodo(todo);
  if (!current.some(({ id }) => id === next.id)) {
    throw new TypeError(`Todo id "${next.id}" does not exist.`);
  }
  return current.map((item) => (item.id === next.id ? next : item));
}

export function removeTodo(todos, id) {
  if (typeof id !== "string") {
    throw new TypeError("Todo id must be a string.");
  }
  return normalizeTodos(todos).filter((todo) => todo.id !== id);
}

export function transitionReconciliationGate(isLocked, event) {
  if (typeof isLocked !== "boolean") {
    throw new TypeError("Reconciliation gate state must be a boolean.");
  }
  if (event === "mutation-failed") return true;
  if (event === "load-succeeded") return false;
  if (event === "load-failed") return isLocked;
  throw new TypeError(`Unknown reconciliation event: ${event}`);
}
