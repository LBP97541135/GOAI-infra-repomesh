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
  /** 需求文档的文件名（上传解析时记录；未上传/旧数据为 null） */
  document_filename: string | null;
  /** §2.1 读模型派生，前端禁止另行映射 */
  state: "open" | "closed";
  /** §2.2 读模型派生，八相枚举，issue 层不得新增第 9 相 */
  phase: Phase;
  phase_note: string;
  round_count: number;
  active_round_id: string | null;
  latest_round_id: string | null;
  pending_decision_count: number;
  /** §2.3：需求已落库但尚未物化成执行计划（无任何轮次）——Org Leader 的
   *  下一步是驱动发现链并物化。与 phase_note「计划 vN 待物化」同一分支派生。 */
  pending_planning: boolean;
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
  /** 上传的需求文档文件名（用户可见；未上传文档时省略） */
  document_filename?: string;
}

/** 需求文档解析结果（POST /issues/parse-document）：上传真实文件后取回的文本。
 *  `chars` 是截断后的实际字符数；`truncated` 为真说明源文档超过 intake 上限。 */
export interface ParsedDocumentView {
  filename: string;
  format: string;
  text: string;
  chars: number;
  truncated: boolean;
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
  /** §3 + Q6：v0.2 决策夹**不含** ReviewRequest，前端不得据此自造决策项。
   *
   *  消费方是 issue 详情页的「监管策略」段（迁移 5-1a）：它与 `execution_mode` 一起
   *  回答「这个项目会不会为某一步停下来等人」。⚠ 这一份是 `sorted()` 的**字母序**
   *  （`api/read_models/service.py`），于是「交付」会排在「执行」前面——渲染前一律过
   *  `display.ts` 的 `orderCheckpoints()` 按流程先后定序，别直接 `join`。
   *
   *  （原注写作「只用于提示『本 issue 设有人工检查点』并链接 main 既有审核台」，
   *  那个按钮已被 5-1a 撤掉：它只在卡点非空时才出现，而活体库多数项目卡点为空，
   *  于是最该说的「这个项目没有任何人工把关」一个字都没有。） */
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

/** 这个成员**由谁托管**——服务端向 Controller 核实的 `containerManaged` 转述：
 *   - `container`：Controller 确认容器归它管；
 *   - `external`：Controller 确认容器**不**归它管（本机 CLI 经 Bridge 接入）；
 *   - `null`：这次探测压根没问（manager 探测不带该字段）——是**未知不是 external**。
 *
 *  `external` 时 `phase` / `runtime_kind` 恒 null：那是容器生命周期词汇，对一个
 *  永远不会被容器化的成员没有主语。Controller 仍会为它回 `Pending`（空 phase 的
 *  默认值），服务端已在 §4.3 投影处扣掉——前端拿到的就是 null。 */
export type AgentRuntimeHosting = "container" | "external";

export interface AgentRuntimeFields {
  kind: AgentRuntimeHosting | null;
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
  /** 操作者声明的正式验证命令；空数组表示尚未配置，不能由前端猜测。 */
  test_commands: string[];
  /** 验证命令读取/写入的测试路径，与命令作为一个配置对维护。 */
  test_paths: string[];
  /** 档案开关（供给侧），见 CONTEXT.md：只决定之后新建的拓扑建不建测试团队，
   *  对存量已建团队无追溯力。null = default 档。TeamsPage 的「测试团队」徽标
   *  以本字段为 join 右表（裁决：身份字段不进 team view，前端 join）。 */
  capability_profile: string | null;
  profiled_at: string;
  /** 拓扑派生：该仓库被多少 team 驻扎 */
  resident_team_count: number;
  /** 与 /issues 同一次 state 派生求和，不会与 issue 列表打架（§4.1） */
  open_issue_count: number;
  active_task_count: number;
  last_delivery_at: string | null;
  teams: Array<{ team_id: string; issue_id: string; runtime_status: TeamRuntimeStatus }>;
}

/** `PATCH /repositories/{id}/verification` 的完整替换请求。 */
export interface RepositoryVerificationUpdate {
  test_commands: string[];
  test_paths: string[];
}

/** 更新端点只需由调用方消费这三个回显字段。 */
export interface RepositoryVerificationView extends RepositoryVerificationUpdate {
  id: string;
}

/** `PATCH /repositories/{id}/capability-profile` 的请求体。`default` 档以 null
 *  拼写——后端把 "default" 字面量当 422 拒绝，存储侧只有 NULL 一种写法。 */
export interface RepositoryCapabilityProfileUpdate {
  capability_profile: string | null;
}

/** 回显消费面：端点返回全量 RepositoryView，前端只消费这两个字段。 */
export interface RepositoryCapabilityProfileView {
  id: string;
  capability_profile: string | null;
}

/** §4.2。`runtime_status`（拓扑记录的**建团结果**，历史事实）与 `runtime.phase`
 *  （Controller 的**当前观测态**，可能不可达）是两个不同事实，**契约明文不得合并**：
 *  合成一个徽标会让 controller 打不通时显示成「团队坏了」，而团队其实建成过。 */
/** 谁把本团队的仓库级任务拆成 worker 任务（裁决 D-2，迁移 0037）。
 *
 *  `server` = 平台在派 leader 任务的同一步里拆解直派（历史行为、绝大多数团队）；
 *  `leader` = 批次停在 leader 任务，等这个团队自己的 external Repository Leader
 *  经 leader-actions 面提交计划。**只有正式 adoption 通道**（materialize 时
 *  controller 说这个 leader 的容器不归它管）才写得出 `leader`，没有任何脚本或
 *  界面开关能设它——所以这个字段是**采用结果的回显**，不是一个可切换的配置。 */
export type TeamDecompositionMode = "server" | "leader";

export interface ConsoleTeamView {
  team_id: string;
  agentteams_team_name: string;
  issue_id: string;
  repository_id: string;
  /** 契约写 string，但实现在 catalog 查不到该仓库时给 null（已核 service.py） */
  repository_name: string | null;
  runtime_status: TeamRuntimeStatus;
  /** 拓扑持久化的**采用结果**，与 `runtime` 探测无关：controller 打不通时它照样为真。 */
  decomposition_mode: TeamDecompositionMode;
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
/** v0.1 §5.4【提案 · 待主脑裁决】`tasks[].evidence`：Runner 载荷里 agent 自述的验证状态。
 *
 *  全部是**转述**，读模型不加判断：`verified` 由 `test_results` 派生（至少一条已记录
 *  的命令、且每条 exit_code 为 0），不读散文；`blockers` 仅当载荷把它声明为字符串
 *  列表时才有值——今天的 Runner 不声明，故存量行恒 `[]`，agent 的原话在 `summary_text`
 *  里逐字保留（契约 6.12）。前端照此渲染：**有 blocker 才数 blocker**，没有就只说
 *  「未验证」并把原话摆出来，不去替 agent 数它没数过的东西。 */
export interface TaskEvidenceView {
  /** false = 这一趟没有可核验的执行记录。终态 succeeded 只说明进程跑完了。 */
  verified: boolean;
  /** agent 结构化声明的 blocker，逐字。未声明即 `[]`（不是「没有 blocker」） */
  blockers: string[];
  /** Runner summary 原文，逐字。**不得摘要、截断或重述** */
  summary_text: string | null;
  test_command: string | null;
  test_results: Array<{ command: string; exit_code: number; summary: string }>;
  /** 只报件数：产物本身尚无可取端点 */
  artifact_count: number;
}

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
  /** §5.4【提案 · 待主脑裁决】coding agent 对自己这一趟的自述（A-18）。
   *
   *  这些字**一直**在 `result_summary` 里——一个 GUI 收到了但从没打开过的 JSON
   *  字符串。live 那条 `runner.completed` 的任务写着 "I could not execute anything
   *  to verify it"、"Please re-run before merging."，界面画的是绿色「已交付」。
   *
   *  `null` **不等于** `verified: false`：前者是「没有任何结构化证据」（superseded、
   *  纯散文回报、Runner 之前的行），后者是「它自己说没验证」。消费方必须分开处理，
   *  把没做过声明的行也标上「未验证」是同一个谎的反面。 */
  evidence: TaskEvidenceView | null;
  /** rework task 创建事件 + recovery action 状态变化合成，可为空 */
  repair_timeline: Array<{ at: string | null; what: string }>;
  /** §5.2：**读模型不做升级判断**，仅转述 recovery plan 里未终态的 MANUAL_INTERVENTION */
  escalated_to_human: boolean;
  /** §8.7.4：最近一条该任务的 TASK_ASSIGNMENT 消息的 created_at（ISO，UTC）。
   *
   *  **是「发出时间」不是「读到时间」**——消息行没有 delivered_at，服务端也没有
   *  编一个。`null` = 从来没有过派工消息，这不是缺数据而是这个字段最响的一句话：
   *  这条任务压根没被派出去过。
   *
   *  重新派工成功后这个时间会前移，那是控制台唯一能给的**事实**级反馈；
   *  「agent 醒没醒」不在读模型的射程内，界面也不猜。 */
  last_dispatched_at: string | null;
}

/** §8.7.4 的两种范围。默认那个**不写任何任务行**——按错的代价是房间里多一条
 *  重复通知；`rerun` 会把已完成的任务送回去重做（真写行、批次重新变成未完成），
 *  所以必须显式要，不能是默认。 */
export type RedispatchScope = "unfinished" | "rerun";

/** §8.7.4 写：重新派工。整轮粒度，与回滚同一条理由（GUI 裁决 #4）——
 *  「哪条卡住了」是判断，判断不住在前端。 */
export interface RoundRedispatchRequest {
  /** 每次按下现取一个新键：它**逐字用作 Matrix transaction id 的推导源**，
   *  沿用旧键会被 homeserver 静默去重，房间里什么都不会多出来。
   *  同一次请求重发（双击、fetch 重试）用同一个键，于是重放而不重发。 */
  idempotency_key: string;
  /** 省略即 `unfinished` */
  scope?: RedispatchScope;
}

export interface RoundRedispatchReceipt {
  round_id: string;
  /** 服务端回显的这次尝试令牌（= 请求里的幂等键） */
  attempt: string;
  scope: RedispatchScope;
  /** 本次重新派工的任务，含仓库 leader 任务 */
  task_ids: string[];
  /** 被送回去重做的已完成任务；`unfinished` 范围下恒为空 */
  reopened_task_ids: string[];
  /** 已终态、被有意跳过的任务——如实说明「没动它们」，不是失败 */
  settled_task_ids: string[];
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
  /** 房间时间线条目可能**没有**发送者 principal：Matrix 发送者没能映射到任何
   *  注册主体时这里是 null（裁决 D-4，服务端 `_timeline_message_item` 如实透传）。
   *  此时 `sender_name` 兜的是原始 Matrix handle——「未解析的发送者，如实呈现它自己」。 */
  sender_agent_id: string | null;
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
  /** **低信号自述**：被评仓库除名字外没有可倚仗的信号（已扫描仓取扫描的
   *  `AutoCard.low_signal` 判定；未扫描仓 facade 全空）。此时分数是猜测——
   *  面板显示「低信号」徽章，**禁止把猜测分数当有据判定呈现**（§3.1 诚实条款）。 */
  low_signal: boolean;
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
 *  `status` 是**大写字符串**（不是枚举类型），值域见 DiscoveryTierStatus。
 *
 *  本批追加两个**恒可下发**的布尔（契约 §2.2 勘正）：
 *   - `is_supplemented`：该仓是 PM 调图预补充进确认名单的，非候选评分产物；
 *   - `graph_conflict`：模型判 EXCLUDED、但图上有一条已确认依赖边把它连到保留仓。
 *  两者缺省语义由契约定死（非真即不渲染），服务端**不省略**恒发 false。 */
export interface ConfirmationResultView {
  repository: string;
  status: DiscoveryTierStatus;
  confidence: number;
  reason: string;
  plan_summary: string;
  plan: ConfirmationRepositoryPlanView | null;
  missing_dependencies: string[];
  is_supplemented: boolean;
  graph_conflict: boolean;
}

/** §2.2 `classification.supplements`：图预补充的证据明细，每仓一条（契约 §2.2）。
 *  `confidence` 是**图边**的置信度（`confirmed` / `declared`），不是 LLM 的；
 *  `via` 是拉它进来的候选仓；`mechanism` 是 forward/reverse_dependencies。 */
export interface SupplementEvidenceView {
  repository: string;
  via: string;
  confidence: string;
  mechanism: string;
  match_reason: string;
}

/** §2.2 `GraphConflictView.edges[].item`：触发复核的那条已确认图边原文。 */
export interface GraphConflictEdgeView {
  producer: string;
  consumer: string;
  confidence: string;
  mechanism: string;
  match_reason: string;
}

/** §2.2 `classification.conflicts`：图与模型的分歧清单（复核建议，非报错）。
 *  `status` 是模型原判（实践中恒 EXCLUDED）；`via` 是图边指向的被保留仓。 */
export interface GraphConflictView {
  repository: string;
  status: string;
  via: string[];
  edges: GraphConflictEdgeView[];
}

/** §2.2 `classification.observations`：低信任观察名单——模型口述、无图边背书、
 *  不在确认名单里的仓。摆给审批人看，要纳入走 adjustments，不自动补充。 */
export interface SupplementObservationView {
  repository: string;
  via: string;
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
 *  三档列表保留 **LLM 原始分档**；生效分档见 §3.1 `effective_tiers`。
 *
 *  `supplements` / `conflicts` / `observations` 是本批（图预补充 + 复核）新增：
 *  图预补充的证据明细、图与模型的分歧清单、低信任观察名单（契约 §2.2）。
 */
export interface DiscoveryClassificationBlock {
  required: ConfirmationResultView[];
  maybe: ConfirmationResultView[];
  excluded: ConfirmationResultView[];
  supplements: SupplementEvidenceView[];
  conflicts: GraphConflictView[];
  observations: SupplementObservationView[];
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
 *  `limit` / `entry_point` **均可选**（实施定死）：`limit` 缺省取服务端配置
 *  `REPOMESH_DISCOVERY_CANDIDATE_LIMIT`（范围 1..50），`entry_point` 缺省 null。
 *  **前端不送，交服务端缺省**——硬编一个就是第二份缺省（同 repositoryScan.ts
 *  不送 max_workers 的取舍）。脚本入口 `POST /discovery` 走自己的请求体，互不干扰。 */
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

/* ── 观测（observability · /api/v1/observe）───────────────────────────────
 * 唯一事实源：src/repomesh/modules/observability/api/models.py —— 后端加字段
 * 必须同步改这里（文件头注释同款约定）。数据来自 observability.llm_usage：
 * 规划侧 LLM 调用（deepseek.py）经线程安全队列落库，再由本页聚合展示。 */

/** 发现链步级汇总；`step` 为 null = 不在发现链内的调用（如回放/兜底路径）。 */
export interface ObserveStepMetric {
  step: number | null;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
}

/** 按模型聚合；`estimated_cost_usd` 按单价表估算（服务端校准常量）。 */
export interface ObserveModelMetric {
  model: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost_usd: number;
}

/** 时间窗口内按天（UTC，YYYY-MM-DD）分桶。 */
export interface ObserveDailyPoint {
  date: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
}

/** 一次失败的 LLM 调用（status != "ok"），最新在前，最多 5 条。 */
export interface ObserveErrorRow {
  created_at: string;
  model: string;
  operation: string;
  finish_reason: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms: number | null;
}

/** 系统级大盘：尾随窗口（GET /observe/summary?days=，默认 7、范围 1..90）。
 *  窗口内无记录时标量归零、`success_rate` / 延迟分位 / 首末时间 / 数组为空。 */
export interface ObserveSummary {
  calls: number;
  success_calls: number;
  error_calls: number;
  success_rate: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  avg_latency_ms: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  first_usage_at: string | null;
  last_usage_at: string | null;
  by_model: ObserveModelMetric[];
  by_step: ObserveStepMetric[];
  daily: ObserveDailyPoint[];
  recent_errors: ObserveErrorRow[];
}

/** Issue 级汇总：一次 issue 的发现链推理消耗了多少。按最近活跃排序。
 *  消费方是用量板块的按 Issue 表。日志的 issue 分组见 IssueLogGroupsResponse，
 *  轨迹的近似 issue 分组见 TraceIssueGroupsResponse——两个源各自独立。 */
export interface ObserveIssueRow {
  issue_id: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  avg_latency_ms: number | null;
  last_usage_at: string | null;
}

export interface ObserveIssuesResponse {
  issues: ObserveIssueRow[];
}

/** 日志按 issue 分组的一行（GET /observe/logs/issues），最近活跃优先。 */
export interface ObserveIssueLogGroup {
  issue_id: string;
  count: number;
  last_at: string | null;
}

export interface IssueLogGroupsResponse {
  issues: ObserveIssueLogGroup[];
}

/** 轨迹按 issue 分组的一行（GET /observe/trace/issues）。
 *  近似归因：trace 会话按 task 键控，只有与 issue 活动窗口（usage ∪ logs，
 *  两侧各放宽 15 分钟）在时间上重叠的会话才被计入 suspected_sessions。 */
export interface ObserveTraceIssueGroup {
  issue_id: string;
  activity_start: string | null;
  activity_end: string | null;
  suspected_sessions: number;
  last_session_at: string | null;
}

export interface TraceIssueGroupsResponse {
  issues: ObserveTraceIssueGroup[];
}

/* ── 观测 · 告警（observability alerts）───────────────────────────────────
 * 唯一事实源：src/repomesh/modules/observability/api/models.py（AlertRuleOut /
 * AlertEventOut）。数据来自 observability.alert_rules + alert_events：
 * 规则按尾随窗口（window_minutes）评估 llm_usage 聚合指标，违反→firing、
 * 恢复→resolved；评估由后台任务（60s）与「立即评估」按钮驱动。 */

/** 规则可评估的指标。服务端校验（SUPPORTED_METRICS），非法值 422。 */
export type AlertMetric =
  | "success_rate"
  | "error_count"
  | "latency_p95_ms"
  | "estimated_cost_usd"
  | "calls";

export type AlertOperator = "lt" | "gt";

/** 告警规则。`window_minutes` 为评估尾随窗口；`enabled` 关闭后不再评估。 */
export interface AlertRule {
  id: string;
  name: string;
  metric: AlertMetric;
  operator: AlertOperator;
  threshold: number;
  window_minutes: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AlertRulesResponse {
  rules: AlertRule[];
}

/** 创建/更新规则体。更新用 PUT 且只送要改的字段（其余省略）。 */
export interface AlertRulePayload {
  name?: string;
  metric?: AlertMetric;
  operator?: AlertOperator;
  threshold?: number;
  window_minutes?: number;
  enabled?: boolean;
}

/** 一次触发/恢复事件。`rule_name` 后端联表回填；`status` = firing | resolved。 */
export interface AlertEvent {
  id: string;
  rule_id: string;
  rule_name: string | null;
  status: "firing" | "resolved";
  message: string;
  value: number;
  window_minutes: number;
  triggered_at: string;
  resolved_at: string | null;
}

export interface AlertEventsResponse {
  events: AlertEvent[];
}

/* ── 观测 · 推理轨迹（observability trace）─────────────────────────────────
 * 唯一事实源：src/repomesh/modules/observability/api/models.py（TraceSessionOut /
 * TraceEventOut）。数据来自 observability.trace_sessions + trace_events：
 * Agent 容器侧的 .copaw 会话文件（对象存储）经 trace_ingest 轮询解析落库。
 * 分页均为 keyset：sessions 按 (first_seen_at, id) 倒序、全局 events 按
 * (ts, id) 倒序、会话事件按 seq 升序；cursor/next_seq 为 null 即无更多页。 */

/** 一个已解析的 Agent 会话（CoPaw）。`event_count` 为该会话已落库事件总数；
 * `parsing_error` 非空 = 整文件解析失败（对象变化前不重试）。 */
export interface TraceSession {
  id: string;
  session_id: string;
  agent_name: string;
  runtime: string;
  source_key: string;
  event_count: number;
  first_seen_at: string;
  parsed_at: string | null;
  parsing_error: string | null;
  object_mtime: string;
  object_size: number;
}

export interface TraceSessionsResponse {
  sessions: TraceSession[];
  next_cursor: string | null;
}

/** 归一化后的事件。会话详情视图里 `agent_name`/`session_external_id` 为 null
 * （由跨会话端点经 JOIN 回填）。`event_type`: chat | tool | skill | mcp |
 * task；`status`: ok | error | skipped。`payload` 为事件负载的关键字段子集。 */
export interface TraceEvent {
  id: string;
  session_id: string;
  seq: number;
  ts: string;
  event_type: string;
  name: string;
  role: string | null;
  summary: string | null;
  token_count: number | null;
  latency_ms: number | null;
  status: string;
  payload: Record<string, unknown> | null;
  agent_name: string | null;
  session_external_id: string | null;
}

export interface TraceEventsResponse {
  events: TraceEvent[];
  next_seq: number | null;
  next_cursor: string | null;
}

/* ── 观测 · 日志（observability logs）───────────────────────────────────────
 * 唯一事实源：src/repomesh/modules/observability/api/models.py（LogEntryOut /
 * LogEntriesResponse）。数据来自 observability.log_entries：RepoMesh 进程
 * 根 logger 经 logging handler → 队列 → 后台批量落库（log_recorder）。
 * `level` 固定五档（DEBUG..CRITICAL）；`source` 为 logger 名；`issue_id` 来自
 * `extra` 标记；分页按 (ts, id) 倒序 keyset。 */

/** 一条结构化日志。`exc_info` 为异常堆栈文本（有异常时非空）。 */
export interface LogEntry {
  id: string;
  ts: string;
  level: string;
  source: string;
  issue_id: string | null;
  message: string;
  exc_info: string | null;
}

export interface LogEntriesResponse {
  logs: LogEntry[];
  next_cursor: string | null;
}

/* ── 平台就绪与 Coding Agent 探测（迁移 3：main 装机向导的读面）─────────────
   两个端点均无鉴权（后端 platform_setup.py 只给 onboard 挂了管理员判定）。 */

/** 后端 `AuthStatus` 三态。`unknown` 是「探不出来」，**不是**「没认上」——
 *  两者措辞必须分开，否则探测不到的适配器会被读成配置错误。 */
export type AdapterAuthStatus = "authorized" | "unauthorized" | "unknown";

export interface CodingAgentAdapterView {
  adapter_id: string;
  display_name: string;
  installed: boolean;
  /** 找到的可执行文件路径；未安装时 null */
  executable: string | null;
  auth_status: AdapterAuthStatus;
  /** 探测细节原文（如 `binary_not_found`），原样展示不翻译 */
  detail: string | null;
  /** 清单登记的执行状态（`unverified` / `superseded_by_driver` / …） */
  execution_status: string;
  runnable_by_verified_driver: boolean;
}

export interface CodingAgentsProbe {
  /** 恒为 `repomesh-api`：探的是 **API 进程所在环境**，不是 Runner 容器 */
  environment: string;
  note: string;
  adapters: CodingAgentAdapterView[];
}

/** `/setup/status` 的九项检查。前五项（model / database / agentteams / matrix /
 *  internal_auth）是 `ready_for_project_creation` 的必检项，其余四项不参与那个
 *  判定——后端的 `required` 元组是唯一事实，前端不另立一套。 */
/** `/setup/status` retains the boolean checks for compatibility. `dependencies`
 * is the setup UI's source of truth for ownership and remediation. */
export type SetupDependencyState =
  | "checking"
  | "ready"
  | "missing"
  | "repairing"
  | "waiting_for_user"
  | "failed"
  | "optional"
  | "pending_onboarding";

export interface SetupDependencyView {
  id: string;
  state: SetupDependencyState;
  owner: "system" | "user" | "onboarding";
  remediation: "automatic" | "user_input" | "optional" | "workflow";
  required: boolean;
  message: string;
}

export interface SetupStatusView {
  ready_for_project_creation: boolean;
  checks: Record<string, boolean>;
  dependencies: SetupDependencyView[];
  counts: { accounts: number; agents: number; repositories: number };
  /** 未通过项的名字，后端已算好 */
  next_actions: string[];
}

/* ── 计划层依赖图（迁移 4：main 单图方案的边语义）──────────────────────────
   `GET /plans/{project_id}/versions/{version}` 的快照行。issue_id 即 project_id
   （契约 v0.2 §0 语义等式），故详情页可以拿 issue_id 直接调。

   **与 §5.4 `dag.edges` 的关系**：那是读模型按 `task_dag.depends_on` 投影出来的
   连线，只有两端；这里是**同一批边的事实源**，多出 status/source 与契约语义
   （interface/agreement）。两者应当一致——单图方案的验收红线就是「读图 ≡ 投影列」，
   所以本类型只用来给既有连线**补语义**，不用来另画一张图。 */

/** 边的确认状态。只有 confirmed 进拓扑投影；candidate 是扫描出的待确认边。 */
export type GraphEdgeStatus = "candidate" | "confirmed";

/** 边的来源：scan=世界层扫描、tm=人工批次顺序派生、llm=集成时模型给出。 */
export type GraphEdgeSource = "scan" | "tm" | "llm";

/** 计划层的一条边。**两端是仓库名不是 id**（与 `execution_batches` 同口径），
 *  故与 §5.4 的节点匹配要按 `name`。序列化用 `from` 别名（`from` 是 TS 保留字
 *  在对象字面量里无妨，但后端 alias 就是它）。 */
export interface PlanGraphEdgeView {
  from: string;
  to: string;
  status: GraphEdgeStatus;
  source: GraphEdgeSource;
  /** 契约接口名；无契约语义的纯依赖边为空串 */
  interface: string;
  agreement: string;
}

/** 一份计划快照。字段与 `PlanSnapshotView` 对齐，前端只消费其中几项。 */
export interface PlanSnapshotView {
  id: string;
  project_id: string;
  plan_version: number;
  created_at: string;
  created_by_agent_id: string | null;
  engineering_spec: string;
  contracts: Array<Record<string, unknown>>;
  task_dag: Array<Record<string, unknown>>;
  execution_batches: string[][];
  graph_edges: PlanGraphEdgeView[];
  execution_plan_id: string | null;
  requirement_text: string | null;
  /** `graph_assisted` = 图里有扫描出的边参与；`llm_only` = 全靠模型给。
   *  null = 老快照没记这一列。 */
  integration_method: string | null;
}

/* ── 历史决策（decision_chain · /api/v1/decision-chains）───────────────────
 * 唯一事实源：src/repomesh/modules/decision_chain/api/models.py —— 后端加字段
 * 必须同步改这里（observability 同款约定）。形状由决策链投影器产出：每步一个
 * 决策单（node），payload_summary 只带指针级摘要，完整事件留在 audit_events。 */

/** §4.1 决策链五步。链内数组即 chain_order；同 (project, step) 重做 version 递增。 */
export type DecisionStep = "classification" | "confirmation" | "integration" | "task" | "pr";

/** §4.1 决策单状态。confirmation 由 approval.state 映射（approved→confirmed 等）；
 *  其余步落第一判词 proposed。merged/closed 出现在后续裁决扩展。 */
export type DecisionStatus =
  | "proposed"
  | "adjusted"
  | "confirmed"
  | "rejected"
  | "changes_requested"
  | "blocked"
  | "superseded"
  | "merged"
  | "closed";

/** §4.1 节点来源：event=事件源投影 / backfill=补投影 / legacy=历史数据。 */
export type DecisionNodeSource = "event" | "backfill" | "legacy";

/** §4.1 actor：llm | human | service；agent_id 对 llm/human 存在。 */
export interface DecisionNodeActorView {
  type: string;
  agent_id: string | null;
}

/** §6.1 链根：需求文本 + 持有它的快照。 */
export interface DecisionRequirementView {
  text: string;
  plan_version: number;
  snapshot_id: string;
}

/** §4.1 一个已投影的决策单。 */
export interface DecisionNodeView {
  decision_id: string;
  event_id: string;
  project_id: string;
  organization_id: string;
  step: DecisionStep;
  version: number;
  status: DecisionStatus;
  actor: DecisionNodeActorView;
  /** 链上父决策（链根的父为 null） */
  upstream_ref: string | null;
  /** §6.2 result/process 指针分账（audit_events / Room 消息） */
  evidence_refs: Record<string, string[]>;
  payload_summary: Record<string, unknown>;
  affected_repository_ids: string[];
  business_time: string;
  recorded_at: string;
  source: DecisionNodeSource;
  event_type: string;
}

/** §6.1 完整追溯输出（审计面消费）。 */
export interface DecisionChainView {
  project_id: string;
  organization_id: string;
  requirement: DecisionRequirementView | null;
  nodes: DecisionNodeView[];
  /** §7 中段缺口：链上有后步节点却缺前步时列出缺失步（缺尾不是缺口） */
  legacy_gaps: string[];
}

/** §6.5 一条相似历史命中（需求级卡片）：检索单位是需求（project_id），
 *  `requirement_text` 是该需求根句（无快照时为 null，卡头回退决策单行）；
 *  卡片次行展示命中依据——与查询最相似的决策单。`score` 为 L3 余弦相似度，
 *  仅语义命中时有值。 */
export interface SimilarDecisionView {
  decision_id: string;
  project_id: string;
  organization_id: string;
  step: DecisionStep;
  version: number;
  status: DecisionStatus;
  affected_repository_ids: string[];
  payload_summary: Record<string, unknown>;
  business_time: string;
  score: number | null;
  requirement_text: string | null;
}

/** §6.5 相似结果：命中最新优先、top_k 截断。空 hits 是合法 200（还没有相似历史
 *  是诚实数据）；`mode` 报告实际服务的方式——semantic 缺 embedding 端点时后端
 *  回退 structural，mode 如实标注。 */
export interface SimilarDecisionsView {
  project_id: string;
  organization_id: string;
  mode: string;
  hits: SimilarDecisionView[];
}

/** §6.5 扩展：跨组织语义检索（按文本搜历史决策）。与按项目锚定的
 *  `SimilarDecisionsView` 不同，这是无锚入口——跨组织按需求检索：每个项目
 *  贡献与其查询最相似的决策单作为命中依据（除非钉死一个 org）。
 *  **无 structural 回退**：缺 embedding 端点 503、embedding 服务错误 502，
 *  诚实配置失败。 */
export interface SemanticSearchView {
  organization_id: string | null;
  query_text: string;
  mode: "semantic";
  hits: SimilarDecisionView[];
}

/** L3 批量向量化回执。无 embedding 端点时服务端返回 {refreshed: 0}（no-op 不是错误）。 */
export interface EmbeddingRefreshView {
  refreshed: number;
}

/* ── 本机 CLI 成员就绪（`repomesh.agent-bridge.readiness.v1`）───────────────
   这一族的三个形状**都是 camelCase**，与本文件其余读模型的 snake_case 不同：
   服务端两处（`ExternalMemberReadinessView.to_wire()` 与 `member_readiness_wire()`）
   就是这么出线的，前端照抄字面不改名。

   租约语义（TTL 45s，判定在服务端）：`ready` = 租约未过期且未停报；`stale` =
   过期但还在一个 TTL 内；`offline` = 更晚，或已报 shutdown。**只有 ready 过物化门**，
   这条判定前端不重算，一律读服务端给的 `status`。 */

export type ExternalMemberReadinessStatus = "ready" | "stale" | "offline";

/** Bridge 能承担的两个角色。组织 leader 不在其中——它驻在 AgentTeams Manager 上，
 *  不跑本机 CLI，所以就绪列表里永远没有它。 */
export type ExternalMemberRole = "repository_leader" | "worker";

/** `GET /runtime/v1/external-members/readiness` 的一行：租约事实 + 服务端此刻派生的
 *  状态。刻意没有 `instanceId` 与 `workspaceRoot`——那是进程身份与操作者机器上的路径，
 *  状态板不发布这两样。 */
export interface ExternalMemberReadinessView {
  agentId: string;
  status: ExternalMemberReadinessStatus;
  role: ExternalMemberRole;
  leaderLane: boolean;
  governedLane: boolean;
  reportedAt: string;
  expiresAt: string;
  /** 报过 shutdown 的时刻；没报过为 null */
  stoppedAt: string | null;
}

export interface ExternalMemberReadinessResponse {
  members: ExternalMemberReadinessView[];
}

/** 就绪门对一个成员的判定。**预检与物化 409 是同一个 wire helper 出的**（服务端注释：
 *  「操作者读回的那一行与拒绝他的那份 body 不能互相打架」），所以这里也是同一个类型。
 *  `reason` 是服务端英文原句（`the readiness lease expired without a renewal` /
 *  `no readiness report` 等），原样展示不翻译。 */
export interface MemberReadinessFact {
  agentId: string;
  role: ExternalMemberRole;
  status: ExternalMemberReadinessStatus;
  reason: string;
}

/** `GET /issues/{issue_id}/discovery/readiness`：物化前的**无副作用**预检。
 *  服务端自述「仅供参考」——租约是关于此刻的断言，渲染出来时它已经旧了一瞬，
 *  真正的权威是物化里那道门。404 = 该 issue 没有进行中的发现轮次。 */
export interface DiscoveryReadinessView {
  checkedAt: string;
  members: MemberReadinessFact[];
}

/** 物化 409 的**结构化** detail（新家族）。这一族与其他 409（检查点未过、计划未生成…）
 *  不同：它可自助解决，且解法就写在成员行里，所以不能糊成一句 detail 原文。 */
export interface ExternalMembersNotReadyDetail {
  code: "external_members_not_ready";
  message: string;
  members: MemberReadinessFact[];
}
