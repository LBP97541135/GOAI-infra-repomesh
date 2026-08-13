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

export type AgentTeamResult = {
  team: { name: string; phase: string; [key: string]: unknown };
  leader: AgentPrincipal;
  members: AgentPrincipal[];
};

export type SetupStatus = {
  ready_for_project_creation: boolean;
  checks: Record<"model" | "database" | "agentteams" | "matrix" | "internal_auth" | "github_app" | "administrator" | "agent_directory" | "repositories", boolean>;
  counts: { accounts: number; agents: number; repositories: number };
  next_actions: string[];
};

export type CodingAgentProbe = {
  adapter_id: string;
  display_name: string;
  installed: boolean;
  executable: string | null;
  auth_status: string;
  detail: string | null;
  execution_status: string;
  runnable_by_verified_driver: boolean;
};

export type RepositoryOnboardResult = {
  organization_id: string;
  repositories: Array<{
    repository_id: string;
    repository_name: string;
    scan: "created" | "reused";
    agent_team: "ready" | "failed";
    detail?: string;
  }>;
};

export type OnboardingJob = {
  id: string;
  organization_id?: string;
  org_url?: string;
  status: "queued" | "running" | "completed" | "failed";
  phase: "queued" | "scanning" | "registering" | "teaming" | "done" | "authorization" | "failed";
  requires_auth?: boolean;
  results: RepositoryOnboardResult["repositories"];
  error?: string | null;
};

export type ProjectTopology = {
  id: string;
  organization_id: string;
  project_id: string;
  organization_leader_id: string;
  execution_mode: "auto" | "supervised" | "manual_controlled";
  operational_status: "active" | "paused" | "cancelled";
  repository_teams: Array<{
    repository_id: string;
    leader_agent_id: string;
    worker_agent_ids: string[];
    runtime_status: string;
  }>;
};

// ---------------------------------------------------------------------------
// PRD → 方案制定链路（repository_intelligence /api/v1）
// ---------------------------------------------------------------------------

export type RequirementAnalysis = {
  sufficient: boolean;
  confidence: number;
  missing_dimensions: string[];
  questions: string[];
  extracted_keywords: string[];
};

export type DiscoveryCandidate = {
  repository_id: string;
  repository_name: string;
  score: number;
  matched_terms: string[];
  rationale: string;
  is_entry_point: boolean;
};

export type RepositoryPlan = {
  changed_apis: string[];
  changed_modules: string[];
  depends_on: string[];
  impacts: string[];
  risk: string;
};

export type ConfirmationResult = {
  repository: string;
  status: string;
  confidence: number;
  reason: string;
  plan_summary: string;
  plan: RepositoryPlan | null;
  missing_dependencies: string[];
};

export type ConfirmationSummary = {
  required: ConfirmationResult[];
  maybe: ConfirmationResult[];
  excluded: ConfirmationResult[];
  supplemented_repos: string[];
  final_repos: string[];
};

export type IntegratedPlan = {
  engineering_spec: string;
  contracts: { producer: string; consumer: string; interface: string; agreement: string }[];
  task_dag: {
    repository: string;
    instruction: string;
    depends_on: string[];
    parallelizable_with: string[];
    tests: string[];
  }[];
  execution_batches: string[][];
};

export type MaterializePayload = {
  engineering_spec: string;
  contracts: IntegratedPlan["contracts"];
  task_dag: IntegratedPlan["task_dag"];
  execution_batches: string[][];
  requirement: string;
  project_id: string;
  leader_agent_id: string;
  idempotency_prefix: string;
  repo_details?: Record<string, RepositoryPlan>;
};

export type MaterializeResult = {
  engineering_spec_id: string;
  contract_spec_ids: string[];
  task_ids: string[];
  skipped_repos: string[];
  plan_id: string | null;
  handoff_doc_ids: string[];
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
  setupStatus: () => request<SetupStatus>("/setup/status"),
  codingAgents: () => request<{ environment: string; note: string; adapters: CodingAgentProbe[] }>("/setup/coding-agents"),
  createNativeAgent: (data: object) => request<AgentPrincipal>("/agents/native", { method: "POST", body: JSON.stringify(data) }),
  onboardRepositories: (data: object) => request<RepositoryOnboardResult>("/setup/repositories/onboard", { method: "POST", body: JSON.stringify(data) }),
  createOnboardingJob: (data: object) => request<OnboardingJob>("/setup/repositories/onboarding-jobs", { method: "POST", body: JSON.stringify(data) }),
  onboardingJob: (jobId: string) => request<OnboardingJob>(`/setup/repositories/onboarding-jobs/${jobId}`),
  onboardingJobs: () => request<OnboardingJob[]>("/setup/repositories/onboarding-jobs"),
  retryOnboardingJob: (jobId: string, data: object) => request<OnboardingJob>(`/setup/repositories/onboarding-jobs/${jobId}/retry`, { method: "POST", body: JSON.stringify(data) }),
  createAgentTeam: (data: object) =>
    request<AgentTeamResult>("/agent-teams", {
      method: "POST",
      body: JSON.stringify(data),
    }),
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
  projects: () => request<ProjectTopology[]>("/projects"),
  requirementAnalysis: (requirement: string) =>
    request<RequirementAnalysis>("/requirement-analysis", {
      method: "POST",
      body: JSON.stringify({ requirement }),
    }),
  discovery: (requirement: string, limit = 8) =>
    request<DiscoveryCandidate[]>("/discovery", {
      method: "POST",
      body: JSON.stringify({ requirement, limit }),
    }),
  confirmation: (
    requirement: string,
    candidateRepos: string[],
    evidence: Record<string, [string, number][]>,
  ) =>
    request<ConfirmationSummary>("/confirmation", {
      method: "POST",
      body: JSON.stringify({
        requirement,
        candidate_repos: candidateRepos,
        discovery_evidence: evidence,
        limit: 15,
      }),
    }),
  integration: (requirement: string, confirmation: ConfirmationSummary) =>
    request<IntegratedPlan>("/integration", {
      method: "POST",
      body: JSON.stringify({ requirement, confirmation }),
    }),
  materialize: (payload: MaterializePayload) =>
    request<MaterializeResult>("/bridge/materialize", {
      method: "POST",
      body: JSON.stringify(payload),
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
