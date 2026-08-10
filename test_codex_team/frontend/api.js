// Canonical browser module; api.mjs re-exports this file for Node/test compatibility.
export const DEFAULT_BACKEND_URL = "http://localhost:8000";

export class TodoApiError extends Error {
  constructor(message, { status = null, cause, outcomeUncertain = false } = {}) {
    super(message, { cause });
    this.name = "TodoApiError";
    this.status = status;
    this.outcomeUncertain = outcomeUncertain;
  }
}

function normalizeBaseUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch (error) {
    throw new TodoApiError("The backend URL is invalid. Use an absolute http:// or https:// URL.", {
      cause: error,
    });
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new TodoApiError("The backend URL must use http:// or https://.");
  }

  const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
  if (!loopbackHosts.has(url.hostname)) {
    throw new TodoApiError("The backend URL override must use a local loopback host.");
  }
  if (url.username || url.password) {
    throw new TodoApiError("The backend URL must not include credentials.");
  }
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new TodoApiError("The backend URL must be an origin without a path, query, or fragment.");
  }

  return url.toString().replace(/\/$/, "");
}

export function resolveBackendBaseUrl(search = "") {
  const configured = new URLSearchParams(search).get("apiBase");
  return normalizeBaseUrl(configured || DEFAULT_BACKEND_URL);
}

function requireString(value, label) {
  if (typeof value !== "string") {
    throw new TodoApiError(`${label} must be a string.`);
  }
  return value;
}

export function validateTodo(value, label = "Todo") {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new TodoApiError(`${label} must be an object.`);
  }
  if (typeof value.id !== "string") {
    throw new TodoApiError(`${label}.id must be a string.`);
  }
  if (typeof value.title !== "string") {
    throw new TodoApiError(`${label}.title must be a string.`);
  }
  if (typeof value.completed !== "boolean") {
    throw new TodoApiError(`${label}.completed must be a boolean.`);
  }
  return { id: value.id, title: value.title, completed: value.completed };
}

async function readJson(response, context, outcomeUncertain = false) {
  try {
    return await response.json();
  } catch (error) {
    throw new TodoApiError(`The backend returned invalid JSON for ${context}.`, {
      status: response.status,
      cause: error,
      outcomeUncertain,
    });
  }
}

async function errorForResponse(response, method) {
  let detail;
  try {
    const payload = await response.json();
    if (payload && typeof payload === "object" && typeof payload.detail === "string") {
      detail = payload.detail.trim();
    }
  } catch {
    // The fallback below is intentionally safe for non-JSON error bodies.
  }

  const isMutation = method !== "GET";
  const explanation = detail || `The backend returned status ${response.status}.`;
  const guidance = isMutation
    ? "Do not retry the mutation blindly. Reload todos before another mutation."
    : "Try loading the todo list again.";
  return new TodoApiError(`${explanation} ${guidance}`, {
    status: response.status,
    outcomeUncertain: isMutation && response.status >= 500,
  });
}

function requireId(id) {
  requireString(id, "Todo id");
  if (!id) {
    throw new TodoApiError("Todo id must not be empty.");
  }
  return encodeURIComponent(id);
}

export function createTodoApi({ baseUrl = DEFAULT_BACKEND_URL, fetchImpl = globalThis.fetch } = {}) {
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl);
  if (typeof fetchImpl !== "function") {
    throw new TodoApiError("A fetch implementation is required.");
  }

  async function request(path, { method = "GET", body, expectedStatus, context }) {
    const isMutation = method !== "GET";
    const headers = { Accept: "application/json" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }

    let response;
    try {
      response = await fetchImpl(`${normalizedBaseUrl}${path}`, {
        method,
        headers,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
    } catch (error) {
      const guidance = isMutation
        ? "The mutation outcome is uncertain. Do not retry it blindly; reload todos to reconcile first."
        : "Check that it is running, then reload the todo list.";
      throw new TodoApiError(
        `Could not reach the backend at ${normalizedBaseUrl}. ${guidance}`,
        { cause: error, outcomeUncertain: isMutation },
      );
    }

    if (response.status !== expectedStatus) {
      throw await errorForResponse(response, method);
    }
    if (expectedStatus === 204) {
      return undefined;
    }
    return readJson(response, context, isMutation);
  }

  return {
    baseUrl: normalizedBaseUrl,

    async listTodos() {
      const payload = await request("/api/todos", {
        expectedStatus: 200,
        context: "the todo list",
      });
      if (payload === null || typeof payload !== "object" || !Array.isArray(payload.todos)) {
        throw new TodoApiError('The backend response must contain a "todos" array.');
      }
      return payload.todos.map((todo, index) => validateTodo(todo, `todos[${index}]`));
    },

    async addTodo(title) {
      requireString(title, "Todo title");
      if (!title.trim()) {
        throw new TodoApiError("Todo title must not be empty.");
      }
      const payload = await request("/api/todos", {
        method: "POST",
        body: { title },
        expectedStatus: 201,
        context: "the created todo",
      });
      return validateTodo(payload, "Created todo");
    },

    async toggleTodo(id, completed) {
      if (typeof completed !== "boolean") {
        throw new TodoApiError("Todo completion state must be a boolean.");
      }
      const payload = await request(`/api/todos/${requireId(id)}`, {
        method: "PATCH",
        body: { completed },
        expectedStatus: 200,
        context: "the updated todo",
      });
      return validateTodo(payload, "Updated todo");
    },

    async deleteTodo(id) {
      await request(`/api/todos/${requireId(id)}`, {
        method: "DELETE",
        expectedStatus: 204,
        context: "todo deletion",
      });
    },
  };
}
