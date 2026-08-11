/** 交付读模型契约 v0.1 的 TypeScript 类型。
 *  唯一来源：docs/contracts/delivery-read-model-v0.1.md（dbd2b1a）。
 *  契约是唯一事实——本文件只转写 §2/§3/§4 的 JSON 形状，禁止添加契约外字段；
 *  契约修订时本文件同步修订。 */

/** §2 交付阶段（读模型推导，前端只渲染） */
export type Phase =
  | "contract"
  | "plan"
  | "execute"
  | "validate"
  | "release"
  | "delivered"
  | "failed"
  | "archived";

/** §5.1 任务展示 6 态（后端唯一映射，前端只渲染） */
export type DisplayStatus = "pending" | "running" | "repairing" | "blocked" | "succeeded" | "failed";

/** §3 任务后端 7 态（原样透出） */
export type BackendTaskStatus =
  | "assigned"
  | "in_progress"
  | "blocked"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "superseded";

/** §5.3 门禁展示 4 态（后端唯一映射，前端只渲染） */
export type GateDisplay = "open" | "blocked" | "running" | "waiting";

/** §5.3 RepositoryDelivery 12 态（原样透出） */
export type RepositoryDeliveryStatus =
  | "pending"
  | "pr_open"
  | "ci_pending"
  | "ci_failed"
  | "review_pending"
  | "review_changes_requested"
  | "ready_to_merge"
  | "merge_requested"
  | "merged"
  | "manual_intervention"
  | "compensation_pending"
  | "compensated";

export type GovernanceDecisionValue = "ready" | "blocked" | "rollback_required";

/* ------------------------------------------------------------------ §2 列表 */

/** 契约 v0.2 §2 `GET /issues` 单条。形状于 2026-08-11 冻结（后端四连提交
 *  b08240f..f7b2df9，合并于 fd40e53）。
 *
 *  诚实降级三处，均为**契约明文的恒 null**，前端显「未接入」不得编造：
 *   - `issue_key`：无 Project 注册表（§0/§6.1），前端显 issue_id 短版；
 *   - `operational_status` / `execution_mode`：来自 project.agent_topologies，
 *     联调种子上该表为空 → 恒 null。**null 不等于 active**，徽标须「有值才渲染」；
 *   - `opened_by_name`：AgentTeams **资源名**（rm-worker-01 这类），与 §4.2 的
 *     `sender_name` 同源同精度，**不是人名**——渲染保留 AGENT 前缀语义。 */
export interface IssueListItemView {
  issue_id: string;
  issue_key: string | null;
  /** 无任何来源时为 null（后端已做轮次→拓扑→开票 agent 三级兜底） */
  organization_id: string | null;
  title: string;
  requirement_text: string | null;
  /** §2.1 读模型派生，前端禁止另行映射 */
  state: "open" | "closed";
  /** §2.2 读模型派生，八相枚举，issue 层不得新增第 9 相 */
  phase: Phase;
  phase_note: string;
  round_count: number;
  active_round_id: string | null;
  latest_round_id: string | null;
  pending_decision_count: number;
  repository_count: number;
  team_count: number;
  /** §2.1：paused **不影响** state，前端以独立徽标呈现 */
  operational_status: "active" | "paused" | "cancelled" | null;
  execution_mode: "auto" | "supervised" | "manual_controlled" | null;
  opened_by_agent_id: string | null;
  opened_by_name: string | null;
  opened_at: string;
  /** §2.3：取不到时间源时回退 opened_at，不编造 */
  updated_at: string;
}

/** §2.5：两个计数**不受 state 与分页影响**，但**受 organization_id 影响**
 *  （计数与列表必须同一隔离域，否则切工作区后标签数与列表内容打架）。
 *  计数与条目同源于一次 state 派生，不存在两套判定。 */
export interface IssueListResponse {
  issues: IssueListItemView[];
  open_count: number;
  closed_count: number;
  /** §2.4 + Q7：offset 不透明游标，语义同 §4.1 events */
  next_cursor: string | null;
}

/** 契约 v0.3 §1.2：issue 写入（POST /issues）。organization_id / title 有意缺席——
 *  前者由主体所属组织唯一决定，后者是读模型对需求文本的截断派生（防双源）。
 *  幂等键由客户端生成：每次逻辑创建一个**新随机键**，重试沿用同键（§1.3——
 *  key 派生 project_id，低熵键会跨用户碰撞归并）。响应即 §2 单条投影，
 *  201=首建 / 200=幂等重放。 */
export interface IssueIntakeRequest {
  requirement_text: string;
  created_by_agent_id: string;
  idempotency_key: string;
}

/** 契约 v0.3 §2.2：工作区（Organization）注册表单条。 */
export interface OrganizationView {
  organization_id: string;
  name: string;
  created_at: string;
  /** agent_directory 派生：该组织活跃 principal 数 */
  agent_count: number;
}

export interface OrganizationsResponse {
  organizations: OrganizationView[];
}

/** 契约 v0.3 §2.3：创建工作区 = 建组织 + 同请求登记 Org Leader。
 *  幂等键语义同 §1.3（客户端随机新键、重试同键）。 */
export interface OrganizationCreateRequest {
  name: string;
  leader_resource_name?: string | null;
  idempotency_key: string;
}

/** §2.3 诚实边界：leader 是期望态登记行，响应不含任何运行时断言。 */
export interface OrganizationCreateResponse {
  organization_id: string;
  name: string;
  created_at: string;
  leader_agent_id: string;
}

// ───────────────── 契约 v0.2 §3 / §5：issue 详情与房间读模型 ─────────────────

export interface IssueRoundView {
  /** = execution_plan_id = v0.1 的 delivery_id（§0 语义等式） */
  round_id: string;
  phase: Phase;
  status: string;
  plan_version: number;
  /** §3：取该轮次 PlanSnapshot 的时间，**无快照为 null**（A8 勘正漏标） */
  created_at: string | null;
  updated_at: string;
}

export interface IssueRepositoryRef {
  repository_id: string;
  name: string;
  team_id: string | null;
  /** 恒 null：拓扑不记录仓库在 issue 中的角色语义（§3） */
  role_in_issue: string | null;
}

/** 拓扑持久化的**建团结果**（§3 与 §4.1/§4.2 同一枚举，只定义一次）。 */
export type TeamRuntimeStatus = "pending" | "ready" | "failed";

export interface IssueTeamRef {
  team_id: string;
  agentteams_team_name: string;
  repository_id: string;
  /** 拓扑记录的**建团结果**（历史事实）；与 §4.2 的 runtime.phase（当前观测态）
   *  是两个不同事实，契约明文不得合并 */
  runtime_status: TeamRuntimeStatus;
}

export interface HumanGrantView {
  human_principal_id: string;
  role: string;
  code_access: "none" | "read" | "write";
}

/** §3：在 §2 单条的**全部字段**之上追加，故继承 IssueListItemView 而不重抄字段表。 */
export interface IssueDetailView extends IssueListItemView {
  rounds: IssueRoundView[];
  repositories: IssueRepositoryRef[];
  teams: IssueTeamRef[];
  contract: DeliveryContractView | null;
  human_grants: HumanGrantView[];
  /** §3 + Q6：v0.2 决策夹**不含** ReviewRequest。本字段只用于提示「本 issue 设有
   *  人工检查点」并链接 main 既有审核台，前端不得据此自造决策项 */
  required_checkpoints: string[];
}

export interface RoomMemberView {
  agent_id: string;
  /** AgentTeams 资源名（rm-leader-a-api 这类），**不是人名** */
  name: string | null;
  role: string;
}

/** §5.1。`members` **按房间类型不同**：teamRoom = [repository_leader, worker…]，
 *  leaderDM = [repository_leader, organization_leader]——它描述「谁能读这个房间」，
 *  照搬团队成员会误述房间语义（后端 2026-08-11 实调修正）。 */
export interface RoomListItemView {
  room_id: string;
  /** 由字段位置决定（room_id=teamRoom / leader_room_id=leaderDM），不猜 */
  kind: "team_room" | "leader_dm";
  issue_id: string;
  team_id: string;
  repository_id: string;
  /** §7.3 勘误：与 §4.2 同一派生，catalog 查不到 repository_id 时为 null */
  repository_name: string | null;
  members: RoomMemberView[];
  /** 空房间为 null 且 message_count:0——**不装填占位消息** */
  last_message: { at: string; kind: string; subject: string; sender_agent_id: string } | null;
  message_count: number;
  /** §5.3：`该仓有 in_progress 任务` 派生，**不是 Matrix presence**。
   *  文案不得写「在线」。 */
  live: boolean;
}

/** 未建团的 issue 返回 `{"rooms": []}` 且 **HTTP 200**（不是 404）——空态不是错误。 */
export interface RoomListResponse {
  rooms: RoomListItemView[];
}

export type RoomStreamSource = "message" | "governance" | "gate" | "runner";

/** §5.2 + Q4 **硬约束**：只有真实房间消息才可渲染成聊天气泡（头像 + 发送者）；
 *  governance / gate / runner 是控制台**投影事实**，必须系统条目样式、无头像气泡，
 *  不得让读者以为某个 agent 在房间里说过这句话。
 *
 *  判据用 **`message !== null`**，不要比对 `source` 字符串：后端保证投影条目由
 *  一个无法附加 message 载荷的构造函数生成，故 message 恒 null（契约 §7.2）。
 *  这样即便将来新增 source 值，漏判也只会退化成系统条目，不会退化成假气泡。 */
export interface RoomStreamItemView {
  at: string;
  source: RoomStreamSource;
  room_id: string;
  /** 非 message 源恒 null（结构性保证，非约定） */
  message: (CollaborationMessageView & { room_id: string }) | null;
  /** 人类可读摘要。message 源也会带（取 subject），渲染气泡时用 body 而非本字段 */
  text: string | null;
  repository_id: string | null;
  task_id: string | null;
  /** 稳定源引用，兼作排序决胜键（沿用 v0.1 §4.1），可做跳转锚点 */
  payload_ref: string | null;
}

export interface RoomStreamPage {
  items: RoomStreamItemView[];
  next_cursor: string | null;
}

/** §5.4 单仓 DAG·PLAN·SPEC 纸面。 */
export interface RepositoryPlanView {
  issue_id: string;
  repository_id: string;
  plan_version: number;
  dag: {
    nodes: Array<{ repository_id: string; name: string; batch_index: number; is_focus: boolean }>;
    edges: Array<{ from_repository_id: string; to_repository_id: string }>;
    /** §5.5：恒为 repository（graph_edges 列已持久化但恒空，不投影） */
    granularity: "repository";
    edge_source: "task_dag.depends_on";
  };
  execution_batches: string[][];
  /** 无匹配为 null → 显「本仓无独立 spec，适用项目工程契约」（§5.4）。
   *  status 真实枚举以实现为准：契约原文的 `submitted` 不存在（已勘误）。 */
  spec: {
    specification_id: string;
    kind: "repository" | "task";
    status: "draft" | "in_review" | "approved" | "frozen" | "superseded";
    revision: number;
    goal: string;
    acceptance: string[];
    allowed_paths: string[];
    forbidden_paths: string[];
    tests: string[];
  } | null;
  /** ENGINEERING kind 是项目级，不混入 spec */
  engineering_contract: DeliveryContractView | null;
}

/* ------------------------------------------ 契约 v0.2 §4 网格 / 团队 / 花名册 */

/** §4.4 运行时代理块的**三态**，压不成一个布尔——三者含义不同，混在一起会撒谎：
 *   - `null`：AgentTeams 未配置，或 Controller 说没有这个资源（404）——无事实可报；
 *   - `{ reachable: false }`：探测失败或超时，**HTTP 仍是 200**。契约硬性要求：
 *     持久化那一半本来可读，不该因运行时不可达整页失败——这是**降级不是故障**，
 *     文案不得写成「团队坏了」；
 *   - `{ reachable: true, ... }`：Controller 当前观测值。
 *
 *  判据写 `runtime?.reachable`：null 与 false 都落到「无观测值」，只有 true 分支
 *  才有字段可读，漏判只会退化成「未接入」，不会退化成编造的运行时状态。 */
export type RuntimeBlock<T> = ({ reachable: true } & T) | { reachable: false } | null;

/** §4.2 TeamRuntimeRef 实际可得字段（`RuntimeSnapshot` 全字段 nullable，已核实现）。 */
export interface TeamRuntimeFields {
  phase: string | null;
  ready_workers: number | null;
  total_workers: number | null;
}

/** §4.3 WorkerRuntimeRef / ManagerRuntimeRef 实际可得字段。
 *  `awake` / `uptime_seconds` **恒 null**：Controller 响应里没有任何时间字段，
 *  而 DesiredRuntimeState 是我们**下发的期望态**不是观测态——拿它冒充观测即编造。
 *  两者都只能显「未接入」，补齐路径是 AgentTeams Controller 暴露启动时间戳。 */
export interface AgentRuntimeFields {
  phase: string | null;
  runtime_kind: string | null;
  matrix_user_id: string | null;
  room_id: string | null;
  message: string | null;
  awake: null;
  uptime_seconds: null;
}

/** §4.1 单条。`auto_card` **不投影**（发现证据未按 project 存储，v0.1 §6.10），
 *  故仓库卡片没有「证据」一栏可填，页面以一句说明交代而不是每张卡糊一个占位。 */
export interface ConsoleRepositoryView {
  repository_id: string;
  name: string;
  url: string;
  description: string;
  topics: string[];
  languages: string[];
  profiled_at: string;
  /** 拓扑派生：该仓库被多少 team 驻扎 */
  resident_team_count: number;
  /** 与 /issues 同一次 state 派生求和，不会与 issue 列表打架（§4.1） */
  open_issue_count: number;
  active_task_count: number;
  last_delivery_at: string | null;
  teams: Array<{ team_id: string; issue_id: string; runtime_status: TeamRuntimeStatus }>;
}

/** §4.2。`runtime_status`（拓扑记录的**建团结果**，历史事实）与 `runtime.phase`
 *  （Controller 的**当前观测态**，可能不可达）是两个不同事实，**契约明文不得合并**：
 *  合成一个徽标会让 controller 打不通时显示成「团队坏了」，而团队其实建成过。 */
export interface ConsoleTeamView {
  team_id: string;
  agentteams_team_name: string;
  issue_id: string;
  repository_id: string;
  /** 契约写 string，但实现在 catalog 查不到该仓库时给 null（已核 service.py） */
  repository_name: string | null;
  runtime_status: TeamRuntimeStatus;
  team_room_id: string | null;
  leader_room_id: string | null;
  leader: RoomMemberView;
  workers: RoomMemberView[];
  runtime: RuntimeBlock<TeamRuntimeFields>;
}

export type AgentRole = "organization_leader" | "repository_leader" | "worker";

/** §4.3。`status` 是 agent_directory 的**启用态**（active|disabled），
 *  **不是醒睡观测态**——原型花名册里的「在岗 / 执行中 / 休眠」没有数据源，
 *  醒睡见 `AgentRuntimeFields.awake` 的说明。 */
export interface ConsoleAgentView {
  agent_id: string;
  organization_id: string;
  role: AgentRole;
  status: "active" | "disabled";
  /** AgentTeams 资源名（rm-worker-01 这类），**不是人名** */
  agentteams_resource_name: string;
  leader_agent_id: string | null;
  repository_id: string | null;
  repository_name: string | null;
  responsibility_paths: string[];
  /** 拓扑反查；未驻扎任何团队时为 null */
  team_id: string | null;
  issue_id: string | null;
  active_task_count: number;
  runtime: RuntimeBlock<AgentRuntimeFields>;
}

export interface ConsoleRepositoriesResponse {
  repositories: ConsoleRepositoryView[];
}

export interface ConsoleTeamsResponse {
  teams: ConsoleTeamView[];
}

export interface ConsoleAgentsResponse {
  agents: ConsoleAgentView[];
}

export interface DeliveryListItem {
  /** null = §0 虚拟草稿交付（尚未 materialize） */
  delivery_id: string | null;
  title: string;
  phase: Phase;
  phase_note: string;
  pending_decision_count: number;
  updated_at: string;
}

export interface DeliveryProjectGroup {
  project_id: string;
  /** nullable：Project 实体/注册表未落地前为 null（§6.9） */
  project_key: string | null;
  title: string;
  deliveries: DeliveryListItem[];
}

export interface DeliveryListResponse {
  projects: DeliveryProjectGroup[];
  /** v0.1 数据量下恒为 null，游标语义保留待后续实现 */
  next_cursor: string | null;
}

/* ------------------------------------------------------------ §3 全貌聚合 */

export interface DeliveryProjectInfo {
  project_id: string;
  /** nullable：Project 实体/注册表未落地前为 null（§6.9） */
  project_key: string | null;
  /** 暂以 plan snapshot requirement_text 截断，Project 落地后切换 */
  title: string;
  /** nullable：plan snapshot.requirement_text */
  requirement_text: string | null;
  created_at: string;
}

export interface DeliveryContractView {
  specification_id: string;
  version: number;
  status: string;
  goal: string;
  acceptance: string[];
  constraints: string[];
  allowed_paths: string[];
  /** specification 新增可选字段，随读模型同批实现（契约 §6.2） */
  forbidden_paths: string[];
  tests: string[];
  /** nullable：specification 暂缓（§6.2），v0.1 恒为 null */
  non_goals: string[] | null;
  /** nullable：specification 暂缓（§6.2），v0.1 恒为 null */
  release_rules: Record<string, unknown> | null;
}

export interface DeliveryRepositoryInfo {
  repository_id: string;
  name: string;
  evidence: string | null;
}

export interface DeliveryPlanView {
  plan_version: number;
  status: string;
  current_batch_index: number;
  execution_batches: string[][];
  /** 由 ChangeSet depends_on 拓扑排序导出 */
  merge_order: string[];
}

export interface RepairStep {
  at: string;
  what: string;
}

export interface DeliveryTaskView {
  task_id: string;
  task_key: string | null;
  repository_id: string;
  title: string;
  backend_status: BackendTaskStatus;
  display_status: DisplayStatus;
  agent: string | null;
  /** 1 + 同仓 rework 链长度（§5.2） */
  attempt: number;
  depends_on: string[];
  result_summary: string | null;
  repair_timeline: RepairStep[];
  /** §5.2：仅转述 recovery plan 的 MANUAL_INTERVENTION，读模型不做升级判断 */
  escalated_to_human: boolean;
}

export interface CiCheckView {
  check_name: string;
  passed: boolean;
  summary: string;
}

export interface ReviewView {
  reviewer: string;
  state: string;
  summary: string;
}

export interface RepositoryDeliveryView {
  repository_id: string;
  task_id: string;
  status: RepositoryDeliveryStatus;
  gate_display: GateDisplay;
  pull_request_url: string | null;
  pull_request_number: number | null;
  head_sha: string;
  base_sha: string;
  branch_name: string;
  depends_on: string[];
  merge_order: number;
  ci_checks: CiCheckView[];
  required_checks: string[];
  required_approvals: number;
  reviews: ReviewView[];
  /** 仅 pre-merge 状态有意义；status ∈ merge_requested/merged/compensation_pending/compensated 时为 null（889464e） */
  merge_gate: { allowed: boolean; reasons: string[] } | null;
  merge_sha: string | null;
}

export interface GovernanceDecisionView {
  id: string;
  repository_id: string;
  head_sha: string;
  decision: GovernanceDecisionValue;
  decided_by_agent_id: string;
  reason: string;
  decided_at: string;
}

export interface RecoveryPlanView {
  trigger: string;
  reason: string;
  actions: unknown[];
}

export interface ChangeSetView {
  change_set_id: string;
  status: string;
  merge_cursor: number;
  repositories: RepositoryDeliveryView[];
  governance_decisions: GovernanceDecisionView[];
  recovery_plans: RecoveryPlanView[];
}

export interface ValidationSnapshotView {
  id: string;
  status: string;
  candidate_heads: Record<string, string>;
  environment_hash: string;
  expires_at: string;
}

export interface DeliveryDiffView {
  repository_id: string;
  run_id: string;
  commit_sha: string;
  changed_files: string[];
  /** nullable：Runner 暂未采集 ±行数（§6.3），v0.1 恒为 null */
  diffstat: Record<string, unknown> | null;
}

export interface DeliveryAggregate {
  delivery_id: string;
  project: DeliveryProjectInfo;
  /** 整体可为 null：该交付未建 ENGINEERING spec（契约 4744c71） */
  contract: DeliveryContractView | null;
  repositories: DeliveryRepositoryInfo[];
  plan: DeliveryPlanView;
  tasks: DeliveryTaskView[];
  change_set: ChangeSetView | null;
  validation_snapshot: ValidationSnapshotView | null;
  diffs: DeliveryDiffView[];
  /** nullable：无 token/成本采集（§6.4），v0.1 恒为 null */
  cost: Record<string, unknown> | null;
  matrix_room_id: string | null;
  trace_id: string | null;
}

/* ------------------------------------------------- §4 事件、消息与决策 */

/** §4.1：`deny` 在 v0.1 不应由后端产出（出现即契约违约）；回放夹具可用于演示叙事 */
export type DeliveryEventKind = "runner" | "matrix" | "gate" | "plan" | "deny";

export interface DeliveryEventItem {
  at: string;
  kind: DeliveryEventKind;
  text: string;
  task_id: string | null;
  repository_id: string | null;
  payload_ref: string | null;
}

export interface DeliveryEventsPage {
  items: DeliveryEventItem[];
  next_cursor: string | null;
}

/** §4.2 CollaborationMessageView 直投影 + 追认附加字段（1df9ebf：direction/
 *  sender_name/recipient_name/created_at）。方向以 direction 辨识，勿假定恒单向。
 *  `room_id` 为 v0.2 §5.2 前置改动补投影（与房间流共用同一投影函数）。
 *  ⚠ id/repository_id/task_id 不在契约文本字段表内——已横向问询后端核实中
 *  （在响应里=待补追认注记；不在=幻影照删），暂保留。 */
export interface CollaborationMessageView {
  id: string;
  kind: string;
  subject: string;
  body: string;
  sender_agent_id: string;
  sender_name: string | null;
  recipient_agent_id: string;
  recipient_name: string | null;
  repository_id: string | null;
  task_id: string | null;
  room_id: string | null;
  status: string;
  event_id: string | null;
  correlation_id: string | null;
  created_at: string;
  direction: string;
}

/** §4.2 **不分页**（契约 5152f48 明文澄清）：一次交付的消息量以「一屏读完」为设计
 *  前提，需要翻页的是房间流（§5.2 `/rooms/{room_id}/stream`），不在此重复一套游标。
 *  此处曾按 §4.1 events 类推多写过一个 `next_cursor`，是消费方猜字段，已清除。 */
export interface DeliveryMessagesPage {
  items: CollaborationMessageView[];
}

/** §4.3：v0.1 仅 approve|watch；clarify 无后端实体（§6.5），只存在于前端回放模式 */
export type DecisionItemKind = "approve" | "watch";

export type DecisionAction = "approve_merge" | "view_evidence";

export interface DecisionItem {
  id: string;
  kind: DecisionItemKind;
  title: string;
  body: string;
  repository_id: string | null;
  head_sha: string | null;
  created_at: string;
  actions: DecisionAction[];
}

export interface DecisionsResponse {
  items: DecisionItem[];
}

/* --------------------------------------------------------- §4.4/§4.5 写端点 */

export interface GovernanceDecisionRequest {
  change_set_id: string;
  repository_id: string;
  /** 必填：head-bound，SHA 漂移即 409 */
  head_sha: string;
  decision: GovernanceDecisionValue;
  /** 必填：决策主体（bearer 为共享动作 token，无法承载身份）；
   *  须为同组织活跃 ORGANIZATION_LEADER 或该仓库 REPOSITORY_LEADER，否则 403 */
  decided_by_agent_id: string;
  reason: string;
  /** 幂等语义为内容重放去重（相同决策重放 no-op、版本不涨） */
  idempotency_key: string;
}
