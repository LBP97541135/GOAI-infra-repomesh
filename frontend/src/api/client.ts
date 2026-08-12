/** 读模型 API typed client。端点与 JSON 形状唯一来源：
 *  docs/contracts/ 交付读模型契约 v0.1/v0.2/v0.3 全部消费端点。 */
import type {
  ConsoleAgentsResponse,
  ConsoleOrgScanRequest,
  ConsoleRepoScanRequest,
  ConsoleRepositoriesResponse,
  ConsoleTeamsResponse,
  DecisionsResponse,
  DeliveryAggregate,
  DeliveryEventsPage,
  DiscoveryAnalysisRequest,
  DiscoveryApprovalRequest,
  DiscoveryCandidatesRequest,
  DiscoveryStepRequest,
  DiscoveryTaskView,
  DiscoveryTriggerAccepted,
  DiscoveryView,
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
  ScanTaskView,
  UrlIdentification,
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

/** 环境默认配置的 client：baseUrl/token 取 Vite 环境变量。各数据源模块共用这一处，
 *  避免每个文件抄一份同样的工厂。 */
export function defaultClient() {
  return createApiClient({
    baseUrl: import.meta.env.VITE_API_BASE ?? "",
    token: import.meta.env.VITE_API_TOKEN ?? "",
  });
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

    /** §4.2。`withRuntime: false` 时 runtime 字段常在、值恒 null（契约 §7.3 勘正），
     *  且不发任何 Controller 请求（实测 0.10s vs 默认 true 的 2.12s）——
     *  首屏用 false，运行时列另发一次填。 */
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

    /** 添加仓库 A-0：**无鉴权、纯解析、无出站**（后端与读端点同一 router），
     *  所以可以随用户输入防抖直调，不必怕它打到平台上去。裸路径而非 console
     *  命名空间——它不是 console 专属的写面，是一次字符串判定。 */
    identifyRepositoryUrl: (url: string) =>
      request<UrlIdentification>(
        config,
        "GET",
        `/repositories/url-type?url=${encodeURIComponent(url)}`,
      ),

    /** A-1：**202** + 任务对象（不是结果），进度靠 `getScanTask` 轮询。
     *  能提前拒的（本地路径 / allowlist 外 host）仍在 202 之前以 400 拒掉。 */
    scanOrganization: (payload: ConsoleOrgScanRequest) =>
      request<ScanTaskView>(config, "POST", `/console/repositories/scan-org`, payload),

    /** A-1：同上，单仓面。组织 URL 发到这里 → 400（服务端复核徽标判定，不轻信）。 */
    scanRepository: (payload: ConsoleRepoScanRequest) =>
      request<ScanTaskView>(config, "POST", `/console/repositories/scan-repo`, payload),

    /** A-2：轮询进度。**404 = 任务状态随进程重启丢失**（端点 detail 自述），
     *  不是坏 id，调用方据此提示「重扫安全」而不是「找不到该任务」。 */
    getScanTask: (taskId: string) =>
      request<ScanTaskView>(
        config,
        "GET",
        `/console/repositories/scan-tasks/${encodeURIComponent(taskId)}`,
      ),

    /* ── 契约 v0.4 发现链（批次 B）───────────────────────────────────────── */

    /** §3.1 发现链读投影。**从未发起发现的 issue 返 200 空块**（不是 404）；
     *  `step` / `step_state` 由读模型按 §3.2 判定，前端只渲染不自判。 */
    getDiscovery: (issueId: string) =>
      request<DiscoveryView>(config, "GET", `/issues/${issueId}/discovery`),

    /** §4.5 轮询。**404 = 任务状态随进程重启丢失**（端点 detail 自述）——
     *  此时改读 `getDiscovery` 即可判断该步到底落没落，不必重跑。 */
    getDiscoveryTask: (issueId: string, taskId: string) =>
      request<DiscoveryTaskView>(
        config,
        "GET",
        `/issues/${issueId}/discovery/tasks/${encodeURIComponent(taskId)}`,
      ),

    /** §4.3 Step 0 需求分析（202）。`force_continue: true` 走 §4.6 的强行继续留痕，
     *  此时**不重跑 LLM**，只在既有 analysis 上记 forced_continue。 */
    postDiscoveryAnalysis: (issueId: string, payload: DiscoveryAnalysisRequest) =>
      request<DiscoveryTriggerAccepted>(config, "POST", `/issues/${issueId}/discovery/analysis`, payload),

    /** §4.3 Step 1 候选评分（202）。前置未满足（分析未通过且未强行继续）→ 409。 */
    postDiscoveryCandidates: (issueId: string, payload: DiscoveryCandidatesRequest) =>
      request<DiscoveryTriggerAccepted>(config, "POST", `/issues/${issueId}/discovery/candidates`, payload),

    /** §4.3 Step 2 三档分类（202）。候选为空 → 409。 */
    postDiscoveryClassification: (issueId: string, payload: DiscoveryStepRequest) =>
      request<DiscoveryTriggerAccepted>(config, "POST", `/issues/${issueId}/discovery/classification`, payload),

    /** §4.3 Step 3 生成计划（202）。**审批 v1 必经**：approval 非 approved → 409。 */
    postDiscoveryPlan: (issueId: string, payload: DiscoveryStepRequest) =>
      request<DiscoveryTriggerAccepted>(config, "POST", `/issues/${issueId}/discovery/plan`, payload),

    /** §5.2 分档审批（**同步** 200，无 LLM 调用）。`evidence_version` 漂移 → 409。
     *  响应体形状契约未定义，故不消费——按 §4.5 同一条，写完一律重取读投影。 */
    postDiscoveryApproval: (issueId: string, payload: DiscoveryApprovalRequest) =>
      request<unknown>(config, "POST", `/issues/${issueId}/discovery/approval`, payload),

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
