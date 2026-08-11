export type Account = {
  id: string;
  username: string;
  display_name: string;
  is_admin: boolean;
  active: boolean;
};

export type ReviewRequest = {
  id: string;
  project_id: string;
  checkpoint: string;
  evidence_version: string;
  title: string;
  summary: string;
  status: "pending" | "approved" | "rejected" | "changes_requested";
  repository_id: string | null;
  requested_by_agent_id: string | null;
  resolved_by_human_id: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentPrincipal = {
  id: string;
  organization_id: string;
  role: "organization_leader" | "repository_leader" | "worker";
  leader_agent_id: string | null;
  repository_id: string | null;
  agentteams_resource_name: string;
  status: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(body.detail || `请求失败 (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export const api = {
  me: () => request<Account>("/auth/me"),
  login: (username: string, password: string) =>
    request<{ account: Account }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  bootstrap: (username: string, password: string, displayName: string) =>
    request<Account>("/auth/bootstrap", {
      method: "POST",
      body: JSON.stringify({ username, password, display_name: displayName }),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  accounts: () => request<Account[]>("/auth/accounts"),
  agents: () => request<AgentPrincipal[]>("/agents"),
  createAccount: (data: object) =>
    request<Account>("/auth/accounts", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  createTopology: (data: object) =>
    request<object>("/projects/topologies", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  reviews: (status = "") =>
    request<ReviewRequest[]>(
      `/review-requests${status ? `?status=${status}` : ""}`,
    ),
  decide: (review: ReviewRequest, decision: string, reason: string) =>
    request<object>(`/projects/${review.project_id}/checkpoint-decisions`, {
      method: "POST",
      body: JSON.stringify({
        review_request_id: review.id,
        decision,
        reason,
      }),
    }),
  controlProject: (projectId: string, action: string) =>
    request<object>(`/projects/${projectId}/control`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
};
