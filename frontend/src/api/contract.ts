/** 交付读模型契约的 TypeScript 类型。
 *  转写 docs/contracts/ 交付读模型契约 v0.1/v0.2/v0.3 全部消费端点的 JSON 形状。
 *  契约是唯一事实——禁止添加契约外字段；契约修订时本文件同步修订。 */

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

/** §5.1 Task：后端 7 态 → **展示 6 态**（读模型内唯一实现，前端禁止另行映射）。
 *  映射表在 v0.1 §5.1：assigned→pending / in_progress→running / in_progress 且存在
 *  未终态 rework 链→repairing / blocked→blocked / succeeded→succeeded /
 *  failed|cancelled→failed；superseded 在列表里被过滤掉。
 *  消费方拿到的就是这 6 个字面值之一，`backend_status` 只作审计用不参与展示判定。 */
export type TaskDisplayStatus = "pending" | "running" | "repairing" | "blocked" | "succeeded" | "failed";

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

/** 契约 v0.3 §1.2：issue 写入（POST /issues）。title 有意缺席——它是读模型对
 *  需求文本的截断派生（防双源）。organization_id 是**交叉校验位而非事实源**
 *  （§6 S-4）：归属仍由主体所属组织唯一决定，带上它声明「我以为自己在哪个
 *  工作区操作」，与主体不一致 → 403（leader id 取错工作区花名册的护栏）。
 *  幂等键由客户端生成：每次逻辑创建一个**新随机键**，重试沿用同键，最短
 *  8 字符（§1.3/§6 S-5——键空间按工作区隔离后碰撞半径限于同工作区）。
 *  响应即 §2 单条投影，201=首建 / 200=幂等重放。 */
export interface IssueIntakeRequest {
  requirement_text: string;
  created_by_agent_id: string;
  idempotency_key: string;
  organization_id?: string;
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

/** §3 轮次条目。
 *
 *  **A-4 勘正（2026-08-12，8100 只读实调）**：`plan_version` 与 `updated_at` 契约
 *  原文写作非空，live 上却双双为 null。三个时间/版本字段同一来源（该轮
 *  PlanSnapshot），`created_at` 早已按 A8 标成可空，另外两个是同一处漏标——
 *  **该轮快照落库之前，三个一起是 null**，这是常态不是异常态。
 *
 *  两个活标本实测**逐字段完全一致**（除 round_id）：
 *   - `5c1b3567-42be-588d-b475-ab9cb0e03689` / 轮次 `8d8a3370…`：物化半途中断；
 *   - `35e66beb-cd03-5aa5-83c8-00a3db31d9c7` / 轮次 `53ff1fd8…`：物化成功、尚未开工。
 *  两者的 `rounds[0]`、聚合的 `plan` / `tasks` / `change_set` / `matrix_room_id`、
 *  `rooms` 全部同形。**读模型没有给出区分这两条来路的字段**——消费方不得据此
 *  判断「是不是坏了」，那是把一个信号读成一个原因。
 *
 *  类型按**实测**标可空，不按契约原文标——把已知会是 null 的字段声明成非空，
 *  编译器就不许消费方检查它，`dayLabel(round.updated_at)` 的 `.match` 白屏就是
 *  这么来的。契约原文的修订另行提给后端，前端不等它。 */
export interface IssueRoundView {
  /** = execution_plan_id = v0.1 的 delivery_id（§0 语义等式） */
  round_id: string;
  phase: Phase;
  status: string;
  /** 无 PlanSnapshot 为 null（A-4 勘正；契约原文写作 `number`） */
  plan_version: number | null;
  /** §3：取该轮次 PlanSnapshot 的时间，**无快照为 null**（A8 勘正漏标） */
  created_at: string | null;
  /** 同上，无快照为 null（A-4 勘正；契约原文写作 `string`） */
  updated_at: string | null;
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
  /** 契约 v0.4 §3.3：发现链两个标量，**恒存在**（从未发起发现时为 1 / "idle"），
   *  与 §3.1 的 `step`/`step_state` 同源同实现。
   *
   *  **消费时机（契约明文）**：只供详情页在**不打开发现面板**时出徽标（如「发现 3/4 ·
   *  待审批」）。面板一旦打开，内部渲染一律以 §3.1 专用端点为准——这两个标量**取不到
   *  `running_task_id`，无法据以轮询**，拿它们驱动面板会得到一个永远不知道任务何时
   *  结束的界面。列表 `GET /issues` 不加这两个字段（IA 里没有它们的位置）。
   *
   *  本批（B-1/B-2）不做徽标，先把类型对齐，免得徽标落地时又改一次形状。 */
  discovery_step: 1 | 2 | 3 | 4;
  discovery_state: "idle" | "running" | "failed" | "done";
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
    /** §5.4 勘正（2026-08-11）：`repository_id` **可为 null**——`execution_batches`
     *  存的是仓库**名**，catalog 解析不到（名字不在册，或在 issue 域外重名歧义、
     *  域内优先后仍无唯一解）时**节点保留、id 为 null**：丢掉节点会让批次缺项、
     *  布局就错了。故消费方不得拿 `repository_id` 当列表 key 或跳转参数——
     *  用 `name + batch_index` 组合键。`is_focus` 在 null 节点恒 false（服务端
     *  判据是 `node_id is not None and node_id == repository_id`）。 */
    nodes: Array<{ repository_id: string | null; name: string; batch_index: number; is_focus: boolean }>;
    /** 两端服务端保证已解析且都落在 `nodes` 内（解析不到或不在任何批次里的边被丢弃），
     *  故连线时不必判空。**但丢弃只进服务端日志**：消费方不得自称这是完整依赖图。 */
    edges: Array<{ from_repository_id: string; to_repository_id: string }>;
    /** §5.5：恒为 repository（graph_edges 列已持久化但恒空，不投影） */
    granularity: "repository";
    edge_source: "task_dag.depends_on";
  };
  /** 装的是仓库**名**不是 id（节点侧 null 的来源即在此） */
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
/** §4.3 契约枚举：Controller 回报的运行时适配器种类。 */
export type RuntimeKind = "openclaw" | "copaw" | "hermes" | "openhuman" | "repomesh-runner";

export interface AgentRuntimeFields {
  phase: string | null;
  runtime_kind: RuntimeKind | null;
  matrix_user_id: string | null;
  room_id: string | null;
  message: string | null;
  awake: null;
  uptime_seconds: null;
}

/** §4.1 单条。`auto_card`（发现证据）**按仓库已存、本版不渲染**（M-13 勘正：
 *  数据随 RepositoryProfile 落库有源，端点暂不投影是版本取舍，不是存储缺失）。
 *  仓库卡片没有证据栏，页面以页脚一句话交代而不是每张卡糊一个占位；渲染已入
 *  backlog，届时按 AutoCard 全量五字段原样投影。 */
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

/* ------------------------------------------- 添加仓库（批次 A · 扫描三端点）
 *
 * 形状唯一来源：`repository_intelligence/api/models.py` 的 UrlIdentification /
 * ScanTaskView / ConsoleOrgScanRequest / ConsoleRepoScanRequest。 */

/** `GET /repositories/url-type`（无鉴权、纯解析、无出站，故可随输入防抖直调）。
 *
 *  判定的**唯一事实源在后端**（detect_platform 是 Python）：前端不镜像这套规则，
 *  徽标只回显本响应——镜像一份 TS 判定必然与 Python 漂移，而漂移的那天两边都
 *  自认为对。
 *
 *  ⚠ 它**不兼做可扫描性探测**：allowlist 之外的 host 照样被判成 group /
 *  single_repo。让一个不收凭据的端点兼当 allowlist 神谕，等于把运维配置泄给
 *  任何人；「能不能扫」由提交后的 400 回答。 */
export interface UrlIdentification {
  url: string;
  url_type: "single_repo" | "group" | "unknown";
  platform: "github" | "gitlab" | "local";
  /** 单仓时=将注册的仓库名；group / unknown 时为 null */
  repository_name: string | null;
}

/** 扫描任务（`POST scan-org|scan-repo` 的 202 体 = `GET scan-tasks/{id}` 的体）。
 *
 *  **进程内记录，不落库**：API 一重启就没了，轮询到 404 不是「坏 id」而是
 *  「状态丢了」（端点 detail 自述），重扫幂等所以这不致命。 */
export interface ScanTaskView {
  task_id: string;
  kind: "organization" | "repository";
  url: string;
  status: "running" | "succeeded" | "failed";
  /** 组织扫描在平台枚举返回前**恒 0**：显示「n / ?」，不编一个没人数过的总数 */
  total: number;
  scanned: number;
  /** 最近**完成**的仓库，不是正在扫的那个 */
  last_scanned_repository: string | null;
  /** running 期间恒 0（全部读完才写 catalog），失败态同样恒 0 ——
   *  三个计数只在 `succeeded` 后是终值，其余状态下不得当结果展示 */
  registered: number;
  skipped: number;
  /** **计数，不是清单**：哪些失败、为什么只在服务端日志里。回显出站失败详情是
   *  本仓已经修过一次的洞（S-2），不为便利开倒车；重试粒度=整次扫描（裁决 1）。 */
  failed: number;
  /** 整次扫描失败的原因，措辞与 502 同级的通用文案（有意不回显出站细节） */
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

/** console 面的请求体是**白名单**（`extra="forbid"`）：没有 `github_token` /
 *  `gitlab_token`，多送任何字段 → 422 具名报错。凭据只走服务端 env
 *  （REPOMESH_REPOSITORY_SCAN_GITHUB_TOKEN / _GITLAB_TOKEN），浏览器不持有。 */
export interface ConsoleOrgScanRequest {
  org_url: string;
  /** 1..20，缺省 5。GUI 不暴露，交给服务端缺省。 */
  max_workers?: number;
}

export interface ConsoleRepoScanRequest {
  repo_url: string;
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

/** v0.1 §3 交付聚合的 `tasks[]`（task_orchestration TaskView + DAG 边）。
 *
 *  本文件此前没有转写它——直到 C-4 需要「DAG 节点按执行态着色」才有第一个消费方。
 *  形状取自契约 v0.1 §3 的响应体原文并已对 live 实调核过（8100，只读 GET）。
 *
 *  ⚠ **同一个 repository_id 可以对应多条**：CI rework task 与父任务同仓（§5.2 的
 *  attempt 就是这么数出来的）。所以按仓消费时必须自己面对「多条各说各话」这一形态，
 *  不能默认 find 出第一条当成该仓的状态。 */
export interface DeliveryTaskView {
  task_id: string;
  /** 无 Project 注册表，恒 null（同 issue_key 的降级） */
  task_key: string | null;
  repository_id: string;
  title: string;
  /** 审计用的后端 7 态原值；**展示一律用 `display_status`**（§5.1 是唯一映射实现） */
  backend_status: string;
  display_status: TaskDisplayStatus;
  /** 由 assignee 经 agent_directory 解析出的资源名（不是人名），解析不到为 null */
  agent: string | null;
  /** §5.2：1 + 同仓 rework 链长度 */
  attempt: number;
  /** plan snapshot task_dag，装的是 task_id */
  depends_on: string[];
  result_summary: string | null;
  /** rework task 创建事件 + recovery action 状态变化合成，可为空 */
  repair_timeline: Array<{ at: string | null; what: string }>;
  /** §5.2：**读模型不做升级判断**，仅转述 recovery plan 里未终态的 MANUAL_INTERVENTION */
  escalated_to_human: boolean;
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
  /** §3 一直投影这一列，本文件到 C-4 才转写（第一个消费方是 DAG 执行态着色） */
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
 *  sender_name/recipient_name/created_at；fed7da2：id/repository_id/task_id——
 *  投影一直输出这三列，契约文本已补追认）。方向以 direction 辨识，勿假定恒单向。
 *  `room_id` 为 v0.2 §5.2 前置改动补投影（与房间流共用同一投影函数）。 */
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

/* --------------------------------------------- §4.6 回滚范围与回滚决策（E-1） */

/** 该仓当前是否已 merge。**读模型判定**（merge_sha 有无），前端不自行推。 */
export type RollbackRepositoryState = "merged" | "unmerged";

/** 恢复计划里该仓的第一个动作。`none` = 这仓没有可撤销的东西（既没 merge
 *  也没开过 PR），不是「不确定」。 */
export type RollbackRepositoryAction = "withhold" | "revert_pull_request" | "none";

export interface RollbackRepositoryRow {
  repository_id: string;
  name: string;
  state: RollbackRepositoryState;
  action: RollbackRepositoryAction;
  /** 逆序动作序号（saga 的 sequence 原值）；action=none 时 null */
  step: number | null;
  merge_sha: string | null;
  pull_request_number: number | null;
}

/** §4.6 读投影。**无 ChangeSet 也返 200**（available=false + no_change_set），
 *  只有交付不存在才 404——「本轮没发布过候选」是对话框要讲的状态，不是错误。 */
export interface RollbackScopeView {
  delivery_id: string;
  change_set_id: string | null;
  available: boolean;
  unavailable_reason: "no_change_set" | "nothing_delivered" | null;
  /** 已有未完成 recovery plan：再提交换个理由必 409，界面先说出来 */
  recovery_in_progress: boolean;
  repositories: RollbackRepositoryRow[];
}

/** §4.6 写请求。**没有 repository_id**：粒度是整 change set（GUI 裁决 4），
 *  也**没有 head_sha**：各仓当前 head 由服务端自己读。 */
export interface RollbackRequest {
  change_set_id: string;
  reason: string;
  requested_by_agent_id: string;
  idempotency_key: string;
}

export interface RollbackRecoveryActionView {
  id: string;
  sequence: number;
  kind: string;
  status: string;
  repository_id: string | null;
  run_id: string | null;
  detail: string;
}

export interface RollbackReceipt {
  delivery_id: string;
  change_set_id: string;
  decisions: GovernanceDecisionView[];
  recovery_plan: {
    id: string;
    trigger: string;
    reason: string;
    created_at: string;
    actions: RollbackRecoveryActionView[];
  };
  /** true = 同内容重放，后端零写入 */
  replayed: boolean;
}

/* ------------------------------------------------- 契约 v0.4 发现链（批次 B） */

/** 形状唯一来源：`docs/contracts/delivery-read-model-v0.4.md`（**已裁决 · 生效**）
 *  §2.2（discovery 块）/ §3.1（读投影）/ §4.3+§4.5（写触发与轮询）/ §5.2（审批）。
 *  嵌套的 `ConfirmationResultView` 复用 `repository_intelligence/api/models.py:255`
 *  既有形状（契约 §2.2 明写「不另写第二套序列化」）。
 *
 *  **契约是唯一事实，契约没写的字段一律不加**——本批后端端点尚未合并，任何「先兼容
 *  一下」的猜测字段都会变成第二份真相。存疑处在批次回报里列为悬挂项等裁决。 */

/** 三档的两种大小写**都是契约明文**，不是笔误，也不许在前端归一成一种：
 *   - 小写 `required|maybe|excluded`：§3.1 `effective_tiers[].tier`、§5.2 审批请求
 *     `adjustments[].tier`（GUI 行内下拉的取值域，契约 §1.1 明写「GUI 展示小写」）；
 *   - 大写 `REQUIRED|MAYBE|EXCLUDED`：`ConfirmationResult.status` 与 §2.2
 *     `adjustments[].from/to`（管线自身的字面值，`application/confirmation.py:86`）。
 *  两者出现在**不同字段**上，故类型也分成两个；渲染时的大小写归一只在 display 层
 *  一处实现（契约 §1.1「大小写映射必须只有一处实现」）。 */
export type DiscoveryTier = "required" | "maybe" | "excluded";
export type DiscoveryTierStatus = "REQUIRED" | "MAYBE" | "EXCLUDED";

/** §2.2：步块失败 = 该块 `error` 非空。**不设计假进度**——一步失败就是这一步
 *  error 非空、后续步块保持 null。`message` 是服务端错误原文摘要（≤500 字）。 */
export interface DiscoveryStepError {
  message: string;
  at: string;
}

export interface DiscoveryAnswer {
  question: string;
  answer: string;
}

/** §4.6 强行继续的留痕（快照侧那一份；另一份是 platform 审计事件）。
 *  ⚠ 读投影里叫 `forced_continue`（已发生），写请求里叫 `force_continue`（祈使），
 *  两个拼写都取自契约原文（§2.2 vs §4.6），不是笔误。 */
export interface DiscoveryForcedContinue {
  ignored_question_count: number;
  by_agent_id: string;
  at: string;
}

/** §2.2 `requirement_analysis` 直投影（GUI 步 1 / 管线 Step 0）。 */
export interface DiscoveryAnalysisBlock {
  sufficient: boolean;
  confidence: number;
  missing_dimensions: string[];
  questions: string[];
  extracted_keywords: string[];
  /** 上一次追问的回答；拼接规则唯一实现在服务端（§4.3，前端拼即第二事实源） */
  answers: DiscoveryAnswer[];
  /** 实际送进 LLM 的全文（含已拼入的答复）。**不是** issue 标题的事实源——
   *  §4.3 Q16 裁决 clarify 答复不改写快照 `requirement_text`。 */
  analyzed_requirement: string;
  forced_continue: DiscoveryForcedContinue | null;
  ran_at: string;
  by_agent_id: string;
  error: DiscoveryStepError | null;
}

/** §2.2 candidates.items 单条。
 *
 *  **`repository_id` 不可为 null**（v0.4 正式版实施定死，草案 `uuid|null` 是笔误）：
 *  LLM 与关键词两条产出路径都只能从 catalog 已有的 profile 取 id，不在 catalog 的候选
 *  在评分时就被过滤掉了，故本块内该字段恒为真实 id，消费方**不需要「catalog 未解析」
 *  的渲染分支**。
 *  ⚠ 与 §5.4 计划纸面的 `dag.nodes[].repository_id` 是两回事——那边**确实可为 null**
 *  （那是按名字解析，不是本块的 catalog 直取），不要把这条结论搬过去。 */
export interface DiscoveryCandidateItem {
  repository_id: string;
  repository_name: string;
  score: number;
  matched_terms: string[];
  /** **原样透传，读模型不摘要不截断**（§3.1 诚实条款），前端同样不许摘要 */
  rationale: string;
  is_entry_point: boolean;
}

/** §2.2 `candidates` 直投影（GUI 步 2 / 管线 Step 1）。 */
export interface DiscoveryCandidatesBlock {
  items: DiscoveryCandidateItem[];
  /** Q11：**产出机制的自述**。关键词回退与 LLM 结果形状完全相同，
   *  `false` 时必须显示「关键词回退评分」，**禁止呈现为模型评分**（§3.1 诚实条款）。
   *  ⚠ 不得用 `matched_terms` 是否为空去反推——那是信号对≠原因对。 */
  llm_used: boolean;
  limit: number;
  entry_point: string | null;
  ran_at: string;
  by_agent_id: string;
  error: DiscoveryStepError | null;
}

/** `ConfirmationResultView.plan`。名字与 §5.4 的 `RepositoryPlanView`（计划纸面）
 *  撞车但**是两个不相干的形状**，故在 TS 侧加 Confirmation 前缀区分。 */
export interface ConfirmationRepositoryPlanView {
  changed_apis: string[];
  changed_modules: string[];
  depends_on: string[];
  impacts: string[];
  risk: string;
}

/** `repository_intelligence/api/models.py:255` 既有形状，§2.2 直接复用。
 *  `status` 是**大写字符串**（不是枚举类型），值域见 DiscoveryTierStatus。 */
export interface ConfirmationResultView {
  repository: string;
  status: DiscoveryTierStatus;
  confidence: number;
  reason: string;
  plan_summary: string;
  plan: ConfirmationRepositoryPlanView | null;
  missing_dependencies: string[];
}

/** §2.2 `classification.adjustments`：审批人改档的留痕，与 LLM 原判**并存**。
 *  覆盖写会抹掉「模型说什么、人改成什么」的对照，属删证据。 */
export interface DiscoveryAdjustmentRecord {
  repository: string;
  from: DiscoveryTierStatus;
  to: DiscoveryTierStatus;
  by_agent_id: string;
  at: string;
}

/** §2.2 `classification` 直投影（GUI 步 3 上半 / 管线 Step 2）。
 *  三档列表保留 **LLM 原始分档**；生效分档见 §3.1 `effective_tiers`。 */
export interface DiscoveryClassificationBlock {
  required: ConfirmationResultView[];
  maybe: ConfirmationResultView[];
  excluded: ConfirmationResultView[];
  supplemented_repos: string[];
  adjustments: DiscoveryAdjustmentRecord[];
  ran_at: string;
  by_agent_id: string;
  error: DiscoveryStepError | null;
}

/** §2.2 `approval`（GUI 步 3 下半）。审批 v1 必经：非 approved 时 §4.3 的
 *  生成计划端点 409。 */
export interface DiscoveryApprovalBlock {
  state: "not_requested" | "approved" | "changes_requested";
  /** **已记录的那次决定绑定的指纹，审计用；未审批时为 `null`。**
   *
   *  ⚠ 提交审批时**不要回填这个**——§3.1 的表把两个字段的分工写死了：
   *  「当前这份分档的指纹」是顶层的 `classification_evidence_version`。拿本字段去提交，
   *  未审批时是 null、已审批时是上一次的旧指纹，两种都必然 409。 */
  evidence_version: string | null;
  decided_by_agent_id: string | null;
  reason: string;
  decided_at: string | null;
}

/** §3.1 派生字段：原始分档叠加 adjustments 后的**唯一生效分档来源**。
 *  前端**禁止**自己把 adjustments 叠到 classification 上（状态映射唯一实现在读模型）。
 *
 *  `original_tier` 取值定死（v0.4 正式版）：
 *  | `adjusted` | `original_tier` | 含义 |
 *  | false | **恒 null** | 模型分档，审批人未动 |
 *  | true  | `"maybe"` 等 | 模型分了档，审批人改成了 `tier` |
 *  | true  | null        | 模型从未给该仓库分档，审批人自行加入 |
 *  改档后又改回原档 → `adjusted:false` + `original_tier:null`（没有净改动就不宣称有）。
 *  两种 null 靠 `adjusted` 区分，所以渲染必须先看 `adjusted` 再读 `original_tier`。 */
export interface DiscoveryEffectiveTier {
  repository: string;
  tier: DiscoveryTier;
  adjusted: boolean;
  original_tier: DiscoveryTier | null;
}

/** §3.1 `integration`：集成产物已落草稿快照时的计数（GUI 步 4 / 管线 Step 3）。 */
export interface DiscoveryIntegrationCounts {
  task_dag_count: number;
  batch_count: number;
  contract_count: number;
}

/** §3.1 `materialization`：§8.3 那张物化收据里**客户端可以看的那几栏**
 *  （提案 · 待主脑裁决；实现见 `read_models/service.py:_materialization_receipt`）。
 *
 *  **为什么要投影它**（缺陷 B-12）：物化自 7659c89 起就是可重入的——半截跑完的一轮
 *  再调一次就能补完——但收据从不进读投影，于是「跑砸了一半」和「压根没试过」在界面上
 *  长得一模一样，GUI 也就没有任何诚实的理由把重试入口摆出来。
 *
 *  `idempotency_key` / `prefix` / `plan_fingerprint` **不投影**：那是服务端的重放账本，
 *  发给客户端等于邀请它去伪造一个跟真键撞车的键。成功态那几栏（`task_ids` /
 *  `team_count` / `repositories` / `skipped_repos`）也不投影——轮次、团队、房间早已各自
 *  投影过，第二份副本只是多一次自相矛盾的机会。
 *
 *  **`error` 是服务端原文，原样渲染**（§3.1 诚实条款）。读模型不给判断，只给 `status`：
 *  「卡住了没有」由前端按 `status` 一条决定，别处不许再长出第二套判定。 */
export interface DiscoveryMaterializationReceipt {
  /** `"failed"` = 上一次物化没跑完，这一轮可以重试补完（§8.3）；
   *  `"materialized"` = 已成交。**只有这两个取值**，服务端原样透传。 */
  status: "materialized" | "failed";
  /** 收据写下的时刻（ISO 8601）。 */
  at: string;
  by_agent_id: string;
  /** 失败原因原文。成功态为 null。 */
  error: string | null;
  /** 失败态**恒为 null**——服务端 `_record_failure` 根本不写这一栏（没起来的计划
   *  没有 id 可指）；成功态为该轮执行计划 id。 */
  plan_id: string | null;
}

/** §3.1 `GET /issues/{issue_id}/discovery` 全体。
 *
 *  **issue 存在但从未发起发现 → HTTP 200** 且三块全 null、`step: 1`、
 *  `step_state: "idle"`（不是 404，沿 §7.2「未建团的 issue 返 200 空集」同一口径）；
 *  issue 本身不存在 → 404。
 *
 *  `step` / `step_state` 由**读模型按 §3.2 七条规则按序判定**，是步进器位置的
 *  唯一事实源：前端只渲染，不得用下面任何一块数据自行推断走到了哪一步。 */
export interface DiscoveryView {
  issue_id: string;
  /** 承载 discovery 的**当前草稿快照**版本（§2.3；发现四步与审批都不涨版） */
  plan_version: number;
  step: 1 | 2 | 3 | 4;
  step_state: "idle" | "running" | "failed" | "done";
  running_task_id: string | null;
  requirement_text: string;
  analyzed_requirement: string | null;
  /** 未跑过的步为 **null**，不填空对象冒充「跑过但没结果」（§3.1 诚实条款） */
  analysis: DiscoveryAnalysisBlock | null;
  candidates: DiscoveryCandidatesBlock | null;
  classification: DiscoveryClassificationBlock | null;
  /** **服务端当前分档的指纹——提交审批时回填的就是这个**（§3.1 实施新增）。
   *  分档存在即非空；`classification` 为 null 时为 null。
   *  与 `approval.evidence_version`（上一次决定绑的那份，审计用）不可互相替代。 */
  classification_evidence_version: string | null;
  effective_tiers: DiscoveryEffectiveTier[];
  approval: DiscoveryApprovalBlock;
  integration: DiscoveryIntegrationCounts | null;
  /** 从未物化过 → **null**（同 `integration` 的缺席口径：键在、值为 null）。
   *  收据先于轮次存在：失败的那次没有轮次，却正是最需要被看见的那次。 */
  materialization: DiscoveryMaterializationReceipt | null;
}

/** §4.5 轮询视图。**进程内记录、重启即丢**（沿 A-2 的诚实注记）：404 不是坏 id。
 *  与扫描不同的是这里有**更强的保证**——结果在快照里，重取 `GET …/discovery`
 *  就知道该步到底落没落，不必重跑。
 *
 *  `status === "succeeded"` 之后前端**仍必须重取** `GET …/discovery`：
 *  任务视图不投影结果（避免同一份数据两个序列化）。 */
export interface DiscoveryTaskView {
  task_id: string;
  issue_id: string;
  step: 1 | 2 | 3 | 4;
  status: "running" | "succeeded" | "failed";
  /** Q17：Step 2 是 N 次 LLM 调用，逐候选投影进度；其余步 `total` 恒 1 */
  progress: { done: number; total: number; label: string };
  /** 失败原因原文摘要，与快照里落的同一份 */
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

/** §4.3 + §5.2：**五个写端点（四个触发 + 审批）同形的三字段回执**。
 *
 *  | 情形 | HTTP | `task_id` | `status` |
 *  | 已受理、任务已起 | 202 | 任务 id | `accepted` |
 *  | 同幂等键重放     | 200 | **null** | `replayed` |
 *  | 审批（同步端点） | 200 | **恒 null** | `accepted` / `replayed` |
 *
 *  重放**不给 task_id**：原任务记录是进程内的、可能早被清掉，编一个回去等于承诺一次
 *  答不上来的轮询。所以 `task_id` 非空才轮询，为空就直接重取读投影——两种情形下
 *  「下一步做什么」本来就是同一个动作。
 *
 *  回执**不投影结果**（不回审批块、不回步块）：那是 §3.1 已经拥有的数据，回一份就是
 *  同一事实两处序列化。 */
export interface DiscoveryWriteReceipt {
  task_id: string | null;
  step: 1 | 2 | 3 | 4;
  /** 重放必须可分辨：否则客户端重试会把 adjustments 再追加一遍，分档上凭空多一条改档 */
  status: "accepted" | "replayed";
}

/** §4.3 Step 0 需求分析。**不收 requirement**：需求文本的事实源是快照的
 *  `requirement_text`（存两份即两个事实源）。 */
export interface DiscoveryAnalysisRequest {
  created_by_agent_id: string;
  /** §4.1：最短 8 字符，**随表单生成**，每次逻辑触发新键、重试沿用同键 */
  idempotency_key: string;
  answers?: DiscoveryAnswer[];
  /** §4.6：置 true 时**不重跑 LLM**，只在既有 analysis 上记 forced_continue；
   *  analysis 为 null 时 409（没有追问可忽略） */
  force_continue?: boolean;
}

/** §4.3 Step 1 候选评分。需求文本取 `analyzed_requirement`，不收。
 *  `limit` / `entry_point` **均可选**（实施定死）：`limit` 缺省 10、范围 1..50，
 *  `entry_point` 缺省 null。**前端不送，交服务端缺省**——硬编一个就是第二份缺省
 *  （同 repositoryScan.ts 不送 max_workers 的取舍）。
 *  与既有脚本入口 `POST /discovery` 的缺省 5 **有意不同**，两处各自独立、不统一。 */
export interface DiscoveryCandidatesRequest {
  created_by_agent_id: string;
  idempotency_key: string;
  limit?: number;
  entry_point?: string | null;
}

/** §4.3 Step 2 三档分类 / Step 3 生成计划：请求体只有主体与幂等键。
 *  分类**不收** candidate_repos / discovery_evidence——脚本时代由客户端回传，
 *  GUI 面由服务端从快照的 candidates 块取（让浏览器回传候选即让前端成为事实源）。 */
export interface DiscoveryStepRequest {
  created_by_agent_id: string;
  idempotency_key: string;
}

/** 批次 C-3 `POST /issues/{issue_id}/discovery/materialize`：把已生成的计划快照
 *  变成执行计划、任务与团队。请求体与四个触发同形（主体 + 幂等键）。
 *
 *  它**不是发现链的第五步**：发现四步改的都是同一份草稿快照，本端点建的是执行面的
 *  实体，是整条链的**第二个不可逆动作**（第一个是 merge 审批）。所以步进器仍只有四格。 */
export interface DiscoveryMaterializeRequest {
  created_by_agent_id: string;
  /** §4.1 同款：随弹窗生成的随机 UUID，重试沿用同键 */
  idempotency_key: string;
}

/** C-3 的 **200 同步回执**。与发现四步的三字段回执（202 + 任务句柄）**有意不同形**：
 *  这里没有后台任务可轮询，回的是产物清单本身。
 *
 *  ⚠ `repositories[]` 的元素语义（仓库 id 还是仓库名）主脑给的形状里没有写死，
 *  故本前端**只用它的长度、不解读元素**——把 id 当名字显示出去就是编造一个仓库名。
 *  未决项已上报，定稿后再消费。 */
export interface DiscoveryMaterializeResult {
  plan_id: string;
  task_ids: string[];
  team_count: number;
  repositories: string[];
  /** 重放必须可分辨：否则重试会让人以为又建了一批任务 */
  status: "materialized" | "replayed";
}

/** §5.2 分档审批（同步端点，200）。`adjustments` 与 `decision` **一次提交**：
 *  拆成两个写会造出「改了但没批」的中间态。 */
export interface DiscoveryApprovalRequest {
  /** 活跃 ORGANIZATION_LEADER（§4.1），前端沿用 decisions.ts 的花名册派生单点实现 */
  decided_by_agent_id: string;
  idempotency_key: string;
  decision: "approved" | "changes_requested";
  reason: string;
  adjustments: { repository: string; tier: DiscoveryTier }[];
  /** §5.3：回填 §3.1 顶层的 `classification_evidence_version`（**不是**
   *  `approval.evidence_version`）。与服务端当前分档指纹不符 → 409：
   *  批的必须是它实际看到的那份证据。 */
  evidence_version: string;
}
