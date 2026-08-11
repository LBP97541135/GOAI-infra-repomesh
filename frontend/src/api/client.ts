/** 读模型 API typed client。端点与 JSON 形状唯一来源：
 *  docs/contracts/delivery-read-model-v0.1.md §1-§4。 */
import type {
  ConsoleAgentsResponse,
  ConsoleRepositoriesResponse,
  ConsoleTeamsResponse,
  DecisionsResponse,
  DeliveryAggregate,
  DeliveryEventsPage,
  DeliveryListResponse,
  DeliveryMessagesPage,
  GovernanceDecisionRequest,
  GovernanceDecisionView,
  IssueDetailView,
  IssueIntakeRequest,
  IssueListItemView,
  IssueListResponse,
  OrganizationCreateRequest,
  OrganizationCreateResponse,
  OrganizationsResponse,
  RepositoryPlanView,
  RoomListResponse,
  RoomStreamPage,
} from "./contract";

export class ApiError extends Error {
  readonly status: number;
  readonly url: string;

  constructor(status: number, url: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
  }
}

export interface ApiClientConfig {
  /** 形如 ""（同源）或 "http://127.0.0.1:8000"；路径前缀 /api/v1 固定 */
  baseUrl: string;
  /** Bearer token；空则不带 Authorization 头 */
  token: string;
}

async function request<T>(config: ApiClientConfig, method: string, path: string, body?: unknown): Promise<T> {
  const url = `${config.baseUrl}/api/v1${path}`;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (config.token) headers.Authorization = `Bearer ${config.token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (cause) {
    throw new ApiError(0, url, `无法连接 ${url}：${cause instanceof Error ? cause.message : String(cause)}`);
  }
  if (!res.ok) {
    // FastAPI 错误体 {"detail": "<message>"}（422 时 detail 为数组）
    const raw = await res.text().catch(() => "");
    let detail = raw;
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown };
      if (parsed.detail !== undefined) {
        detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
      }
    } catch {
      /* 非 JSON 体，原样展示 */
    }
    throw new ApiError(res.status, url, `${method} ${path} → HTTP ${res.status}${detail ? ` · ${detail.slice(0, 200)}` : ""}`);
  }
  return (await res.json()) as T;
}

export function createApiClient(config: ApiClientConfig) {
  return {
    /** §2.4：state 默认 open；organization_id 由前端持有并传参（Q2，服务端不猜）；
     *  cursor/limit 语义同 §4.1 events。 */
    listIssues: (opts?: {
      state?: "open" | "closed" | "all";
      organizationId?: string;
      cursor?: string;
      limit?: number;
    }) => {
      const params = new URLSearchParams();
      if (opts?.state) params.set("state", opts.state);
      if (opts?.organizationId) params.set("organization_id", opts.organizationId);
      if (opts?.cursor) params.set("cursor", opts.cursor);
      if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
      const q = params.toString();
      return request<IssueListResponse>(config, "GET", `/issues${q ? `?${q}` : ""}`);
    },

    /** 契约 v0.3 §1：创建 issue（= 首份虚拟草稿快照）。201 首建 / 200 幂等重放，
     *  响应都是 §2 单条投影；403 主体非活跃 Org Leader、404 主体不存在。 */
    createIssue: (payload: IssueIntakeRequest) =>
      request<IssueListItemView>(config, "POST", `/issues`, payload),

    /** 契约 v0.3 §2.2：工作区注册表（console 命名空间，§4.5 裁决同款前缀）。 */
    listOrganizations: () =>
      request<OrganizationsResponse>(config, "GET", `/console/organizations`),

    /** 契约 v0.3 §2.3：创建工作区（建组织 + 登记 Org Leader）。
     *  201 首建 / 200 幂等重放 / 409 同名不同键。 */
    createOrganization: (payload: OrganizationCreateRequest) =>
      request<OrganizationCreateResponse>(config, "POST", `/console/organizations`, payload),

    /** §4.1 + §4.5：三条网格端点收在 `console` 命名空间下——裸 `/repositories`
     *  已被 repository_intelligence 的 catalog 视图先注册占用，挂裸路径会得到一个
     *  永远不可达的端点而 OpenAPI 反显网格的定义（文档与实际行为相反）。
     *  本端点**没有 runtime 块**，故无 with_runtime 参数，实测 0.3s 返回。 */
    listConsoleRepositories: () =>
      request<ConsoleRepositoriesResponse>(config, "GET", `/console/repositories`),

    /** §4.2。`withRuntime: false` 时整块 runtime 省略且不发任何 Controller 请求
     *  （实测 0.10s vs 默认 true 的 2.12s）——首屏用 false，运行时列另发一次填。 */
    listConsoleTeams: (opts?: { withRuntime?: boolean }) =>
      request<ConsoleTeamsResponse>(
        config,
        "GET",
        `/console/teams${opts?.withRuntime === false ? "?with_runtime=false" : ""}`,
      ),

    /** §4.3。同上：探测已并发化（后端 15d9a76），N 条不可达仍收敛到单条超时量级，
     *  但那仍是 ~2s，故首屏不等它。 */
    listConsoleAgents: (opts?: { withRuntime?: boolean }) =>
      request<ConsoleAgentsResponse>(
        config,
        "GET",
        `/console/agents${opts?.withRuntime === false ? "?with_runtime=false" : ""}`,
      ),

    listDeliveries: (cursor?: string) =>
      request<DeliveryListResponse>(config, "GET", `/deliveries${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`),

    getDelivery: (deliveryId: string) =>
      request<DeliveryAggregate>(config, "GET", `/deliveries/${deliveryId}`),

    // §4.1：kind 单值过滤；cursor 为不透明字符串，原样回传续读
    getEvents: (deliveryId: string, opts?: { cursor?: string; kind?: string; limit?: number }) => {
      const params = new URLSearchParams();
      if (opts?.kind) params.set("kind", opts.kind);
      if (opts?.cursor) params.set("cursor", opts.cursor);
      if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
      const q = params.toString();
      return request<DeliveryEventsPage>(config, "GET", `/deliveries/${deliveryId}/events${q ? `?${q}` : ""}`);
    },

    // §4.2 不分页（契约 5152f48）——此处曾有一个猜来的 cursor 参数，已清除
    getMessages: (deliveryId: string) =>
      request<DeliveryMessagesPage>(config, "GET", `/deliveries/${deliveryId}/messages`),

    /** §5.1：未建团的 issue 返回 `{"rooms": []}` 且 HTTP 200，空态不是错误 */
    listRooms: (issueId: string) => request<RoomListResponse>(config, "GET", `/issues/${issueId}/rooms`),

    /** §5.2：room_id 形如 `!rm-team-c-billing:matrix.local`，含 `!` 与 `:` 必须编码；
     *  cursor 语义同 §4.1 events；未知 room_id → 404 */
    getRoomStream: (roomId: string, opts?: { cursor?: string; limit?: number }) => {
      const params = new URLSearchParams();
      if (opts?.cursor) params.set("cursor", opts.cursor);
      if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
      const q = params.toString();
      return request<RoomStreamPage>(config, "GET", `/rooms/${encodeURIComponent(roomId)}/stream${q ? `?${q}` : ""}`);
    },

    /** §5.4：单仓 DAG·PLAN·SPEC 纸面 */
    getRepositoryPlan: (issueId: string, repositoryId: string) =>
      request<RepositoryPlanView>(config, "GET", `/issues/${issueId}/repositories/${repositoryId}/plan`),

    getIssueDetail: (issueId: string) => request<IssueDetailView>(config, "GET", `/issues/${issueId}`),

    getDecisions: (deliveryId: string) =>
      request<DecisionsResponse>(config, "GET", `/deliveries/${deliveryId}/decisions`),

    postGovernanceDecision: (deliveryId: string, payload: GovernanceDecisionRequest) =>
      request<GovernanceDecisionView>(config, "POST", `/deliveries/${deliveryId}/governance-decisions`, payload),

    archiveDelivery: (deliveryId: string) =>
      request<unknown>(config, "POST", `/deliveries/${deliveryId}/archive`),
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;
