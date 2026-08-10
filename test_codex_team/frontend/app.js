import { createTodoApi, resolveBackendBaseUrl } from "./api.js";
import {
  appendTodo,
  normalizeTodos,
  removeTodo,
  replaceTodo,
  transitionReconciliationGate,
} from "./state.js";

const elements = {
  app: document.querySelector("#todo-app"),
  form: document.querySelector("#todo-form"),
  input: document.querySelector("#todo-title"),
  addButton: document.querySelector("#add-button"),
  list: document.querySelector("#todo-list"),
  loading: document.querySelector("#loading-state"),
  empty: document.querySelector("#empty-state"),
  error: document.querySelector("#error-state"),
  errorMessage: document.querySelector("#error-message"),
  retryButton: document.querySelector("#retry-button"),
  status: document.querySelector("#operation-status"),
};

let todos = [];
let hasLoaded = false;
let isLoading = false;
let isAdding = false;
let reconciliationRequired = false;
const pendingTodoIds = new Set();
let api;

function messageFor(error) {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "Something went wrong. Reload the todo list before continuing.";
}

function clearError() {
  elements.error.hidden = true;
  elements.errorMessage.textContent = "";
  elements.retryButton.hidden = true;
  elements.retryButton.textContent = "Retry loading";
}

function showError(error, { canRetryLoad = false, retryLabel = "Retry loading" } = {}) {
  elements.errorMessage.textContent = messageFor(error);
  elements.retryButton.hidden = !canRetryLoad;
  elements.retryButton.textContent = retryLabel;
  elements.error.hidden = false;
}

function announce(message) {
  elements.status.textContent = "";
  requestAnimationFrame(() => {
    elements.status.textContent = message;
  });
}

function findItemControl(id, selector) {
  return [...elements.list.querySelectorAll(".todo-item")]
    .find((item) => item.dataset.id === id)
    ?.querySelector(selector);
}

function isItemMutationBlocked(id) {
  return reconciliationRequired || isLoading || pendingTodoIds.has(id);
}

function updateAppBusy() {
  elements.app.setAttribute("aria-busy", String(isLoading || isAdding || pendingTodoIds.size > 0));
}

function setItemPendingState(id, isPending) {
  const item = [...elements.list.querySelectorAll(".todo-item")]
    .find((candidate) => candidate.dataset.id === id);
  if (!item) return;
  item.setAttribute("aria-busy", String(isPending));
  for (const control of item.querySelectorAll(".todo-toggle, .delete-button")) {
    control.setAttribute("aria-disabled", String(isPending || reconciliationRequired));
  }
  updateAppBusy();
}

function enterReconciliationGate(action) {
  reconciliationRequired = transitionReconciliationGate(
    reconciliationRequired,
    "mutation-failed",
  );
  const messages = {
    add: "The add outcome could not be confirmed. Do not submit the title again. Reload todos to reconcile with the backend.",
    toggle: "The completion outcome could not be confirmed. Reload todos to reconcile before another mutation.",
    delete: "The delete outcome could not be confirmed. Do not retry deletion blindly. Reload todos to reconcile with the backend.",
  };
  showError(new Error(messages[action]), {
    canRetryLoad: true,
    retryLabel: "Reload todos to reconcile",
  });
}

function createTodoItem(todo) {
  const item = document.createElement("li");
  item.className = "todo-item";
  item.dataset.id = todo.id;
  item.dataset.completed = String(todo.completed);
  item.setAttribute("aria-busy", String(pendingTodoIds.has(todo.id)));

  const blocked = isItemMutationBlocked(todo.id);

  const toggle = document.createElement("input");
  toggle.className = "todo-toggle";
  toggle.type = "checkbox";
  toggle.checked = todo.completed;
  toggle.setAttribute("aria-disabled", String(blocked));
  toggle.setAttribute(
    "aria-label",
    `${todo.completed ? "Mark incomplete" : "Mark complete"}: ${todo.title}`,
  );
  toggle.addEventListener("change", () => {
    if (isItemMutationBlocked(todo.id)) {
      toggle.checked = todo.completed;
      return;
    }
    handleToggle(todo.id, !todo.completed);
  });

  const title = document.createElement("span");
  title.className = "todo-title";
  title.textContent = todo.title;

  const deleteButton = document.createElement("button");
  deleteButton.className = "delete-button";
  deleteButton.type = "button";
  deleteButton.textContent = "Delete";
  deleteButton.setAttribute("aria-disabled", String(blocked));
  deleteButton.setAttribute("aria-label", `Delete: ${todo.title}`);
  deleteButton.addEventListener("click", () => {
    if (!isItemMutationBlocked(todo.id)) handleDelete(todo.id);
  });

  item.append(toggle, title, deleteButton);
  return item;
}

function render() {
  updateAppBusy();
  elements.loading.hidden = !isLoading;
  elements.addButton.disabled = isAdding || isLoading || reconciliationRequired || !api;
  elements.addButton.textContent = isAdding ? "Adding…" : "Add todo";
  elements.empty.hidden = isLoading || !hasLoaded || todos.length > 0;
  elements.list.replaceChildren(...todos.map(createTodoItem));
}

async function loadTodos() {
  if (!api || isLoading) return;
  isLoading = true;
  clearError();
  render();

  try {
    todos = normalizeTodos(await api.listTodos());
    hasLoaded = true;
    reconciliationRequired = transitionReconciliationGate(
      reconciliationRequired,
      "load-succeeded",
    );
    announce(todos.length ? `${todos.length} todos loaded.` : "No todos found.");
  } catch (error) {
    reconciliationRequired = transitionReconciliationGate(
      reconciliationRequired,
      "load-failed",
    );
    showError(error, {
      canRetryLoad: true,
      retryLabel: reconciliationRequired ? "Reload todos to reconcile" : "Retry loading",
    });
    announce("Todos could not be loaded.");
  } finally {
    isLoading = false;
    render();
  }
}

async function handleAdd(event) {
  event.preventDefault();
  if (!api || isAdding) return;
  if (reconciliationRequired) {
    elements.retryButton.focus();
    return;
  }

  const title = elements.input.value.trim();
  if (!title) {
    elements.input.setCustomValidity("Enter a title with at least one non-space character.");
    elements.input.reportValidity();
    return;
  }
  elements.input.setCustomValidity("");

  isAdding = true;
  clearError();
  render();
  try {
    const created = await api.addTodo(title);
    todos = appendTodo(todos, created);
    hasLoaded = true;
    elements.input.value = "";
    announce(`Added ${created.title}.`);
  } catch {
    enterReconciliationGate("add");
    announce("Add outcome uncertain. Reload todos before submitting again.");
  } finally {
    isAdding = false;
    render();
    (reconciliationRequired ? elements.retryButton : elements.input).focus();
  }
}

async function handleToggle(id, completed) {
  if (!api || isItemMutationBlocked(id)) return;
  pendingTodoIds.add(id);
  clearError();
  setItemPendingState(id, true);

  let succeeded = false;
  try {
    const updated = await api.toggleTodo(id, completed);
    todos = replaceTodo(todos, updated);
    succeeded = true;
    announce(`${updated.title} marked ${updated.completed ? "complete" : "incomplete"}.`);
  } catch {
    enterReconciliationGate("toggle");
    announce("Update outcome uncertain. Reload todos before another mutation.");
  } finally {
    pendingTodoIds.delete(id);
    render();
    (succeeded
      ? findItemControl(id, ".todo-toggle") || elements.input
      : elements.retryButton
    ).focus();
  }
}

async function handleDelete(id) {
  if (!api || isItemMutationBlocked(id)) return;
  pendingTodoIds.add(id);
  clearError();
  setItemPendingState(id, true);

  let succeeded = false;
  try {
    await api.deleteTodo(id);
    todos = removeTodo(todos, id);
    succeeded = true;
    announce("Todo deleted.");
  } catch {
    enterReconciliationGate("delete");
    announce("Delete outcome uncertain. Reload todos before another mutation.");
  } finally {
    pendingTodoIds.delete(id);
    render();
    (succeeded
      ? elements.list.querySelector(".todo-toggle") || elements.input
      : elements.retryButton
    ).focus();
  }
}

elements.form.addEventListener("submit", handleAdd);
elements.input.addEventListener("input", () => elements.input.setCustomValidity(""));
elements.retryButton.addEventListener("click", loadTodos);

try {
  api = createTodoApi({ baseUrl: resolveBackendBaseUrl(window.location.search) });
  loadTodos();
} catch (error) {
  isLoading = false;
  showError(error);
  render();
}
