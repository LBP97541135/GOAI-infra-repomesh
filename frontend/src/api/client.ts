/** 读模型 API typed client。端点与 JSON 形状唯一来源：
 *  docs/contracts/ 交付读模型契约 v0.1/v0.2/v0.3 全部消费端点。 */
import type {
  CodingAgentsProbe,
  ConsoleAgentsResponse,
  ConsoleOrgScanRequest,
  ConsoleRepoScanRequest,
  ConsoleRepositoriesResponse,
  ConsoleTeamsResponse,
  DecisionChainView,
  DecisionsResponse,
  DeliveryAggregate,
  DeliveryEventsPage,
  DiscoveryAnalysisRequest,
  DiscoveryApprovalRequest,
  DiscoveryCandidatesRequest,
  DiscoveryMaterializeRequest,
  DiscoveryMaterializeResult,
  DiscoveryStepRequest,
  DiscoveryTaskView,
  DiscoveryWriteReceipt,
  EmbeddingRefreshView,
  AlertEventsResponse,
  AlertRule,
  AlertRulePayload,
  AlertRulesResponse,
  DiscoveryView,
  GovernanceDecisionRequest,
  GovernanceDecisionView,
  IssueDetailView,
  IssueIntakeRequest,
  IssueListItemView,
  IssueListResponse,
  IssueLogGroupsResponse,
  LogEntriesResponse,
  ObserveIssuesResponse,
  ObserveSummary,
  OrganizationCreateRequest,
  OrganizationCreateResponse,
  OrganizationsResponse,
  ParsedDocumentView,
  PlanSnapshotView,
  RepositoryPlanView,
  RepositoryVerificationUpdate,
  RepositoryVerificationView,
  RollbackReceipt,
  RollbackRequest,
  RollbackScopeView,
  RoomListResponse,
  RoomStreamPage,
  RoundRedispatchReceipt,
  RoundRedispatchRequest,
  ScanTaskView,
  SemanticSearchView,
  SimilarDecisionsView,
  TraceEventsResponse,
  TraceIssueGroupsResponse,
  TraceSessionsResponse,
  SetupStatusView,
  UrlIdentification,
} from "./contract";
import { browserApiToken } from "../runtimeConfig";

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

async function errorFromResponse(res: Response, method: string, path: string): Promise<string> {
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
  return `${method} ${path} → HTTP ${res.status}${detail ? ` · ${detail.slice(0, 200)}` : ""}`;
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
    throw new ApiError(res.status, url, await errorFromResponse(res, method, path));
  }
  return (await res.json()) as T;
}

/** 环境默认配置的 client：baseUrl/token 取 Vite 环境变量。各数据源模块共用这一处，
 *  避免每个文件抄一份同样的工厂。 */
export function defaultClient() {
  return createApiClient({
    baseUrl: import.meta.env.VITE_API_BASE ?? "",
    token: browserApiToken(),
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

    /** 需求文档真实上传：multipart 解析（与 createIssue 互补——上传取回的纯文本
     *  由弹窗填入需求区、可编辑后再随 issue 提交）。415 格式不支持、413 超限、
     *  422 无可提取文本（如扫描件 PDF），detail 原样上抛。 */
    parseIssueDocument: async (file: File) => {
      const path = "/issues/parse-document";
      const url = `${config.baseUrl}/api/v1${path}`;
      const form = new FormData();
      form.append("file", file);
      const headers: Record<string, string> = { Accept: "application/json" };
      if (config.token) headers.Authorization = `Bearer ${config.token}`;
      // Content-Type 不手设：浏览器会带 multipart 边界，手设反而丢边界导致 422。
      let res: Response;
      try {
        res = await fetch(url, { method: "POST", headers, body: form });
      } catch (cause) {
        throw new ApiError(0, url, `无法连接 ${url}：${cause instanceof Error ? cause.message : String(cause)}`);
      }
      if (!res.ok) {
        throw new ApiError(res.status, url, await errorFromResponse(res, "POST", path));
      }
      return (await res.json()) as ParsedDocumentView;
    },

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

    /** 仓库验证配置是操作者事实，不由扫描器或计划模型猜测；完整替换使重试幂等。 */
    updateRepositoryVerification: (
      repositoryId: string,
      payload: RepositoryVerificationUpdate,
    ) =>
      request<RepositoryVerificationView>(
        config,
        "PATCH",
        `/repositories/${encodeURIComponent(repositoryId)}/verification`,
        payload,
      ),

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

    /** 平台就绪检查（迁移 3）。**无鉴权**：后端 `platform_setup.py` 只给 onboard
     *  那条挂了管理员判定。`checks` 九项里只有前五项参与
     *  `ready_for_project_creation`，那个判定由服务端做，前端不重算。 */
    getSetupStatus: () => request<SetupStatusView>(config, "GET", `/setup/status`),

    /** Coding Agent 探测（迁移 3）。探的是 **API 进程所在环境**，Runner 容器要
     *  自己暴露 probe——响应里的 `note` 就是这句，原样透出不改写。 */
    getCodingAgents: () => request<CodingAgentsProbe>(config, "GET", `/setup/coding-agents`),

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
      request<DiscoveryWriteReceipt>(config, "POST", `/issues/${issueId}/discovery/analysis`, payload),

    /** §4.3 Step 1 候选评分（202）。前置未满足（分析未通过且未强行继续）→ 409。 */
    postDiscoveryCandidates: (issueId: string, payload: DiscoveryCandidatesRequest) =>
      request<DiscoveryWriteReceipt>(config, "POST", `/issues/${issueId}/discovery/candidates`, payload),

    /** §4.3 Step 2 三档分类（202）。候选为空 → 409。 */
    postDiscoveryClassification: (issueId: string, payload: DiscoveryStepRequest) =>
      request<DiscoveryWriteReceipt>(config, "POST", `/issues/${issueId}/discovery/classification`, payload),

    /** §4.3 Step 3 生成计划（202）。**审批 v1 必经**：approval 非 approved → 409。 */
    postDiscoveryPlan: (issueId: string, payload: DiscoveryStepRequest) =>
      request<DiscoveryWriteReceipt>(config, "POST", `/issues/${issueId}/discovery/plan`, payload),

    /** §5.2 分档审批（**同步** 200，无 LLM 调用）。`evidence_version` 漂移 → 409。
     *  回执与四个触发同形（`task_id` 恒 null），**不回投影审批块**——按 §4.5 同一条，
     *  写完一律重取读投影。 */
    postDiscoveryApproval: (issueId: string, payload: DiscoveryApprovalRequest) =>
      request<DiscoveryWriteReceipt>(config, "POST", `/issues/${issueId}/discovery/approval`, payload),

    /** 批次 C-3 物化开工（**同步** 200，无任务句柄可轮询）。409 的原因不止一种
     *  （受控项目的 REPOSITORY_SCOPE 检查点未过、计划尚未生成…），detail 原文
     *  一律原样呈现——把它们归并成一句「物化失败」会把可自助解决的前置问题
     *  伪装成系统故障。 */
    postDiscoveryMaterialize: (issueId: string, payload: DiscoveryMaterializeRequest) =>
      request<DiscoveryMaterializeResult>(config, "POST", `/issues/${issueId}/discovery/materialize`, payload),

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

    /** §5.2：room_id 形如 `!repomesh-team-c-billing:matrix.local`，含 `!` 与 `:` 必须编码；
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

    /** 迁移 4：计划快照原件，用于给 §5.4 的连线补边语义（interface/agreement）
     *  与 `integration_method`。**读端点无鉴权守卫**，走动作 token 通道即可。
     *  404 = 该版本不存在（issue 从未规划，或版本号越界）。 */
    getPlanSnapshot: (projectId: string, version: number) =>
      request<PlanSnapshotView>(
        config,
        "GET",
        `/plans/${encodeURIComponent(projectId)}/versions/${version}`,
      ),

    getIssueDetail: (issueId: string) => request<IssueDetailView>(config, "GET", `/issues/${issueId}`),

    getDecisions: (deliveryId: string) =>
      request<DecisionsResponse>(config, "GET", `/deliveries/${deliveryId}/decisions`),

    postGovernanceDecision: (deliveryId: string, payload: GovernanceDecisionRequest) =>
      request<GovernanceDecisionView>(config, "POST", `/deliveries/${deliveryId}/governance-decisions`, payload),

    archiveDelivery: (deliveryId: string) =>
      request<unknown>(config, "POST", `/deliveries/${deliveryId}/archive`),

    /** §4.6 读：回滚会撤销什么。**逆序第 k 步由服务端给**（读模型跑同一个
     *  recovery planner 的预览），前端只渲染，不按 merge_order 自己数。 */
    getRollbackScope: (deliveryId: string) =>
      request<RollbackScopeView>(config, "GET", `/deliveries/${deliveryId}/rollback-scope`),

    /** §4.6 写：整 change set 回滚。一次调用两个写（每仓 ROLLBACK_REQUIRED
     *  决策堵死 merge gate + 建 operator-requested recovery plan）。
     *  409 = 已有未完成的 recovery plan；detail 原文上抛。 */
    postRollback: (deliveryId: string, payload: RollbackRequest) =>
      request<RollbackReceipt>(config, "POST", `/deliveries/${deliveryId}/rollback`, payload),

    /** §8.7.4 写：重发本轮任务包与点名（缺陷 A-13）。
     *  404 = 无此轮次；409 = 本轮无可派工的任务（尚未物化 / 全部已完成，
     *  两种 detail 措辞不同且都是可行动内容）；503 = 执行面暂时接不住。
     *  一律 detail 原文上抛，由弹窗呈现。 */
    postRoundRedispatch: (roundId: string, payload: RoundRedispatchRequest) =>
      request<RoundRedispatchReceipt>(config, "POST", `/deliveries/${roundId}/redispatch`, payload),

    /* ── 观测（/api/v1/observe）────────────────────────────────────────── */

    /** 系统级大盘。days 缺省 7、范围 1..90（后端 Query ge=1 le=90 校验，越界 422）。
     *  两个端点都要求 `Authorization: Bearer <agent_action_token>`：未配置 503，
     *  不匹配 401。空库不报错——各数组为空、标量归零，前端据此渲染空态。 */
    observeSummary: (days?: number) => {
      const q = days !== undefined ? `?days=${days}` : "";
      return request<ObserveSummary>(config, "GET", `/observe/summary${q}`);
    },

    /** Issue 级汇总：按最近活跃排序，最多 100 条（用量板块的按 Issue 表）。 */
    observeIssues: () => request<ObserveIssuesResponse>(config, "GET", `/observe/issues`),

    /** 日志按 issue 分组（最近活跃优先），供日志页「按 issue」视图。 */
    observeLogIssueGroups: () =>
      request<IssueLogGroupsResponse>(config, "GET", `/observe/logs/issues`),

    /* ── 告警 ─────────────────────────────────────────────────────────── */

    /** 规则列表（含已禁用）。首次访问会种下 3 条默认规则。 */
    alertRules: () => request<AlertRulesResponse>(config, "GET", `/observe/alert-rules`),

    /** 创建规则。metric/operator 非法 422。 */
    createAlertRule: (payload: AlertRulePayload) =>
      request<AlertRule>(config, "POST", `/observe/alert-rules`, payload),

    /** 更新规则（只送要改的字段）。 */
    updateAlertRule: (ruleId: string, payload: AlertRulePayload) =>
      request<AlertRule>(config, "PUT", `/observe/alert-rules/${ruleId}`, payload),

    /** 删除规则（事件级联删除）。 */
    deleteAlertRule: (ruleId: string) =>
      request<void>(config, "DELETE", `/observe/alert-rules/${ruleId}`),

    /** 告警历史（firing + resolved），默认 7 天。 */
    alertEvents: (days?: number) => {
      const q = days !== undefined ? `?days=${days}` : "";
      return request<AlertEventsResponse>(config, "GET", `/observe/alerts${q}`);
    },

    /** 当前正在 firing、未解决的告警。 */
    activeAlerts: () => request<AlertEventsResponse>(config, "GET", `/observe/alerts/active`),

    /** 立即跑一轮评估，返回当前活跃告警（演示/手动触发用）。 */
    evaluateAlerts: () =>
      request<AlertEventsResponse>(config, "POST", `/observe/alerts/evaluate`),

    /* ── 推理轨迹（trace）────────────────────────────────────────────────── */

    /** 会话列表，按 (first_seen_at, id) 倒序 keyset 分页。limit 1..200；
     *  cursor 指向不存在的行时服务端返回空页（不报错）。
     *  issueId 把列表收窄到该 issue 活动窗口（±15 分钟）内的会话——近似归因。 */
    traceSessions: (opts?: {
      agentName?: string;
      issueId?: string;
      limit?: number;
      cursor?: string;
    }) => {
      const params = new URLSearchParams();
      if (opts?.agentName) params.set("agent_name", opts.agentName);
      if (opts?.issueId) params.set("issue_id", opts.issueId);
      if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
      if (opts?.cursor) params.set("cursor", opts.cursor);
      const q = params.toString();
      return request<TraceSessionsResponse>(
        config,
        "GET",
        `/observe/trace/sessions${q ? `?${q}` : ""}`,
      );
    },

    /** 轨迹按 issue 分组（近似归因：issue 活动窗口 × 会话时间重叠），
     *  最近会话优先。供轨迹页「按 Issue」视图。 */
    observeTraceIssueGroups: () =>
      request<TraceIssueGroupsResponse>(config, "GET", `/observe/trace/issues`),

    /** 单会话事件流水，按 seq 升序。未知 session_id → 404。 */
    traceSessionEvents: (
      sessionId: string,
      opts?: { limit?: number; afterSeq?: number },
    ) => {
      const params = new URLSearchParams();
      if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
      if (opts?.afterSeq !== undefined) params.set("after_seq", String(opts.afterSeq));
      const q = params.toString();
      return request<TraceEventsResponse>(
        config,
        "GET",
        `/observe/trace/sessions/${encodeURIComponent(sessionId)}/events${q ? `?${q}` : ""}`,
      );
    },

    /** 跨会话事件流，按 (ts, id) 倒序 keyset 分页；event_type/status 非法 → 422。 */
    traceEvents: (opts?: {
      eventType?: string;
      status?: string;
      agentName?: string;
      limit?: number;
      cursor?: string;
    }) => {
      const params = new URLSearchParams();
      if (opts?.eventType) params.set("event_type", opts.eventType);
      if (opts?.status) params.set("status", opts.status);
      if (opts?.agentName) params.set("agent_name", opts.agentName);
      if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
      if (opts?.cursor) params.set("cursor", opts.cursor);
      const q = params.toString();
      return request<TraceEventsResponse>(
        config,
        "GET",
        `/observe/trace/events${q ? `?${q}` : ""}`,
      );
    },

    /* ── 日志（logs）────────────────────────────────────────────────────── */

    /** 统一日志查询：按 (ts, id) 倒序 keyset 分页。level 非法 → 422；
     *  cursor 指向不存在的行时服务端返回空页。source/query 为大小写不敏感
     *  子串匹配；issue_id 精确匹配。 */
    observeLogs: (opts?: {
      level?: string;
      source?: string;
      issueId?: string;
      query?: string;
      limit?: number;
      cursor?: string;
    }) => {
      const params = new URLSearchParams();
      if (opts?.level) params.set("level", opts.level);
      if (opts?.source) params.set("source", opts.source);
      if (opts?.issueId) params.set("issue_id", opts.issueId);
      if (opts?.query) params.set("query", opts.query);
      if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
      if (opts?.cursor) params.set("cursor", opts.cursor);
      const q = params.toString();
      return request<LogEntriesResponse>(
        config,
        "GET",
        `/observe/logs${q ? `?${q}` : ""}`,
      );
    },

    /* ── 历史决策（decision_chain · /api/v1/decision-chains）────────────── */

    /** §6.1 完整决策链追溯。organization_id 是 L1 命名空间，**可省略**——
     *  审计人员按需求 id 追溯时不一定知道归属组织，省略即跨组织搜索。
     *  未知项目（无节点且无需求）→ 404。要求 Bearer agent_action_token。 */
    getDecisionChain: (projectId: string, organizationId?: string | null) => {
      const q = organizationId ? `?organization_id=${encodeURIComponent(organizationId)}` : "";
      return request<DecisionChainView>(
        config,
        "GET",
        `/decision-chains/${encodeURIComponent(projectId)}${q}`,
      );
    },

    /** §6.5 相似历史：structural（同仓 + 最近，默认）/ semantic（余弦）。
     *  semantic 缺 embedding 端点时后端回退 structural，`mode` 字段如实报告
     *  实际服务的方式；空 hits 是合法 200（还没有相似历史）。 */
    getSimilarDecisions: (
      projectId: string,
      organizationId?: string | null,
      opts?: { mode?: "structural" | "semantic"; queryText?: string; topK?: number },
    ) => {
      const params = new URLSearchParams();
      if (organizationId) params.set("organization_id", organizationId);
      if (opts?.mode) params.set("mode", opts.mode);
      if (opts?.queryText) params.set("query_text", opts.queryText);
      if (opts?.topK !== undefined) params.set("top_k", String(opts.topK));
      const q = params.toString();
      return request<SimilarDecisionsView>(
        config,
        "GET",
        `/decision-chains/${encodeURIComponent(projectId)}/similar${q ? `?${q}` : ""}`,
      );
    },

    /** §6.5 扩展：跨组织语义检索（按文本搜历史决策）。**无 structural 回退**：
     *  缺 embedding 端点 503、embedding 服务错误 502——诚实配置失败，不拿
     *  结构相似冒充语义命中。 */
    semanticSearchDecisions: (
      queryText: string,
      opts?: { organizationId?: string | null; topK?: number },
    ) => {
      const params = new URLSearchParams({ query_text: queryText });
      if (opts?.organizationId) params.set("organization_id", opts.organizationId);
      if (opts?.topK !== undefined) params.set("top_k", String(opts.topK));
      const q = params.toString();
      return request<SemanticSearchView>(
        config,
        "GET",
        `/decision-chains/semantic-search${q ? `?${q}` : ""}`,
      );
    },

    /** L3 管理端点：一次批量向量化全部存量决策单。无 embedding 端点 →
     *  {refreshed: 0}（no-op，不是错误）。要求 Bearer agent_action_token。 */
    refreshDecisionEmbeddings: () =>
      request<EmbeddingRefreshView>(config, "POST", `/decision-chains/embeddings/refresh`),
  };
}
