/** issue 详情页 replay 夹具（CONS-42 骨架）。
 *
 *  形状**逐字段对齐契约 v0.2**（与 CONS-41 的 issues.ts 不同，后者是先于契约的提案）：
 *    §3   GET /issues/{issue_id}                             → IssueDetail
 *    §5.1 GET /issues/{issue_id}/rooms                       → RoomListItem
 *    §5.2 GET /rooms/{room_id}/stream                        → RoomStreamItem
 *    §5.4 GET /issues/{id}/repositories/{rid}/plan           → RepositoryPlan
 *
 *  红线：state / phase / phase_note / live / runtime_status 全部由读模型派生，
 *  前端只渲染不映射（v0.1 §5 + v0.2 §2.1/§2.2/§5.3）。
 *  复用：contract 整块与 message 投影直接用 v0.1 既有类型，契约 §6 禁止另写第二套。 */
import type { CollaborationMessageView, DeliveryContractView, IssueListItemView, Phase } from "../api/contract";

// ─────────────────────────── §3 issue 概览 ───────────────────────────

export interface IssueRound {
  /** = execution_plan_id = v0.1 的 delivery_id（§0 语义等式） */
  round_id: string;
  phase: Phase;
  status: string;
  plan_version: number;
  created_at: string;
  updated_at: string;
}

export interface IssueRepositoryRef {
  repository_id: string;
  name: string;
  team_id: string | null;
  /** §3：拓扑不记录仓库在 issue 中的角色语义，取不到为 null → 显「未接入」 */
  role_in_issue: string | null;
}

export interface IssueTeamRef {
  team_id: string;
  agentteams_team_name: string;
  repository_id: string;
  /** 拓扑记录的**建团结果**（历史事实）；与 §4.2 的 runtime.phase（当前观测态）
   *  是两个不同事实，契约明文不得合并 */
  runtime_status: "pending" | "ready" | "failed";
}

export interface HumanGrant {
  human_principal_id: string;
  role: string;
  code_access: "none" | "read" | "write";
}

/** §3：在 §2 单条的**全部字段**之上追加，故直接继承契约层的 IssueListItemView，
 *  不另抄一份字段表（抄一份就会漂移）。 */
export interface IssueDetail extends IssueListItemView {
  rounds: IssueRound[];
  repositories: IssueRepositoryRef[];
  teams: IssueTeamRef[];
  contract: DeliveryContractView | null;
  human_grants: HumanGrant[];
  /** §3 + Q6：v0.2 决策夹**不含** ReviewRequest。本字段只用于提示「本 issue 设有
   *  人工检查点」并链接 main 既有审核台，前端不得据此自造决策项 */
  required_checkpoints: string[];
}

// ─────────────────────────── §5.1 房间清单 ───────────────────────────

export interface RoomMember {
  agent_id: string;
  name: string | null;
  role: string;
}

export interface RoomListItem {
  room_id: string;
  /** 由字段位置决定（room_id=teamRoom / leader_room_id=leaderDM），不猜 */
  kind: "team_room" | "leader_dm";
  issue_id: string;
  team_id: string;
  repository_id: string;
  repository_name: string;
  members: RoomMember[];
  /** §5.1：空房间为 null 且 message_count:0 —— **不装填占位消息** */
  last_message: { at: string; kind: string; subject: string; sender_agent_id: string } | null;
  message_count: number;
  /** §5.3：由在途 Task 派生，**不是 Matrix presence**（无 presence 数据源，
   *  编造即违约）。徽标文案不得写「在线」，按契约语义写「在途任务」 */
  live: boolean;
}

// ────────────────── §5.2 单房间合并流（本页最硬的渲染约束） ──────────────────

/** ⚠ 契约 §5.2 + Q4 裁决明文：
 *    source === "message" → 房间内**真实发生**的消息 → 常规聊天气泡（头像 + 发送者）
 *    source !== "message" → 控制台**投影事实**，并非房间内真实发生
 *                         → **必须系统条目样式，无头像气泡**
 *  理由：不得让用户以为某个 agent 在房间里说过这句话。
 *  这是契约文本要求，不是渲染建议——渲染分支只允许以 source === "message" 分流。 */
export type RoomStreamSource = "message" | "governance" | "gate" | "runner";

export interface RoomStreamItem {
  at: string;
  source: RoomStreamSource;
  room_id: string;
  /** source=message 时为 v0.1 §4.2 投影 + v0.2 补的 room_id；其余源恒 null */
  message: (CollaborationMessageView & { room_id: string }) | null;
  /** 非 message 源的人类可读摘要；source=message 时为 null */
  text: string | null;
  repository_id: string | null;
  task_id: string | null;
  /** 稳定源引用，兼作排序决胜键（沿用 v0.1 §4.1） */
  payload_ref: string | null;
}

export interface RoomStreamPage {
  items: RoomStreamItem[];
  next_cursor: string | null;
}

// ─────────────────── §5.4 单仓 DAG · PLAN · SPEC 纸面 ───────────────────

export interface RepositoryPlan {
  issue_id: string;
  repository_id: string;
  plan_version: number;
  dag: {
    nodes: Array<{ repository_id: string; name: string; batch_index: number; is_focus: boolean }>;
    edges: Array<{ from_repository_id: string; to_repository_id: string }>;
    /** §5.5：恒为 repository（graph_edges 列存在但恒空，不投影） */
    granularity: "repository";
    edge_source: "task_dag.depends_on";
  };
  execution_batches: string[][];
  /** 无匹配为 null → 显「本仓无独立 spec，适用项目工程契约」（§5.4） */
  spec: {
    specification_id: string;
    kind: "repository" | "task";
    status: "draft" | "submitted" | "approved" | "frozen";
    revision: number;
    goal: string;
    acceptance: string[];
    allowed_paths: string[];
    forbidden_paths: string[];
    tests: string[];
  } | null;
  /** ENGINEERING kind 是项目级，不混入 spec（§5.4） */
  engineering_contract: DeliveryContractView | null;
}

// ══════════════ 夹具：沿用 #7f3d2a10（结账价格修改原因）三仓两轮场景 ══════════════

const ISSUE_ID = "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0042";
const ORG_ID = "0a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d";
const REPO_API = "b1c2d3e4-0001-4a2b-9c3d-4e5f6a7b8c01";
const REPO_WEB = "b1c2d3e4-0002-4a2b-9c3d-4e5f6a7b8c02";
const REPO_DOCS = "b1c2d3e4-0003-4a2b-9c3d-4e5f6a7b8c03";

export const issueDetailFixture: IssueDetail = {
  issue_id: ISSUE_ID,
  issue_key: null,
  organization_id: ORG_ID,
  title: "结账价格修改原因：记录、暴露并在后台展示",
  requirement_text:
    "运营侧需要在订单结账时记录价格被修改的原因（促销、议价、纠错），原因随订单落库并在后台订单详情页展示。价格修改入口不变，新增原因必填校验与审计字段。",
  state: "open",
  phase: "release",
  phase_note: "发布门禁",
  round_count: 2,
  active_round_id: "2ebf564b-3bf2-5af1-ae24-3ccc4dd9d721",
  latest_round_id: "2ebf564b-3bf2-5af1-ae24-3ccc4dd9d721",
  pending_decision_count: 1,
  repository_count: 3,
  team_count: 3,
  operational_status: "active",
  execution_mode: "supervised",
  opened_by_agent_id: "9c8b7a60-1122-4d33-8e44-5f6a7b8c9d00",
  // AgentTeams 资源名，不是人名（渲染保留 AGENT 前缀）
  opened_by_name: "console-demo-org-leader",
  opened_at: "2026-08-09T02:14:00Z",
  updated_at: "2026-08-11T12:20:01Z",
  rounds: [
    {
      round_id: "1a0e3c92-77b1-4c5d-9e0f-1122334455aa",
      phase: "delivered",
      status: "completed",
      plan_version: 1,
      created_at: "2026-08-09T02:20:00Z",
      updated_at: "2026-08-10T08:05:00Z",
    },
    {
      round_id: "2ebf564b-3bf2-5af1-ae24-3ccc4dd9d721",
      phase: "release",
      status: "in_progress",
      plan_version: 2,
      created_at: "2026-08-10T09:00:00Z",
      updated_at: "2026-08-11T12:20:01Z",
    },
  ],
  repositories: [
    { repository_id: REPO_API, name: "saleor-core", team_id: "t-0001", role_in_issue: null },
    { repository_id: REPO_WEB, name: "saleor-dashboard", team_id: "t-0002", role_in_issue: null },
    { repository_id: REPO_DOCS, name: "saleor-docs", team_id: "t-0003", role_in_issue: null },
  ],
  teams: [
    { team_id: "t-0001", agentteams_team_name: "rm-team-a1b2c3", repository_id: REPO_API, runtime_status: "ready" },
    { team_id: "t-0002", agentteams_team_name: "rm-team-d4e5f6", repository_id: REPO_WEB, runtime_status: "ready" },
    { team_id: "t-0003", agentteams_team_name: "rm-team-90cc11", repository_id: REPO_DOCS, runtime_status: "pending" },
  ],
  contract: null,
  human_grants: [{ human_principal_id: "h-0001", role: "delivery_owner", code_access: "write" }],
  required_checkpoints: ["specification", "delivery"],
};

/** §5.1：每仓两条（teamRoom + leaderDM）。docs 团队尚未 ready，其 leaderDM 是空房间
 *  → last_message: null / message_count: 0（「空房间不装满」）。 */
export const roomsFixture: RoomListItem[] = [
  {
    room_id: "!room-core-team:local",
    kind: "team_room",
    issue_id: ISSUE_ID,
    team_id: "t-0001",
    repository_id: REPO_API,
    repository_name: "saleor-core",
    members: [
      { agent_id: "a-lead-01", name: "leader · core", role: "repository_leader" },
      { agent_id: "a-work-01", name: "worker · 597869c4", role: "worker" },
      { agent_id: "a-work-02", name: "worker · b6b2f051", role: "worker" },
    ],
    last_message: {
      at: "2026-08-11T14:31:00Z",
      kind: "assignment",
      subject: "返工指派：修复 core 的失败候选 — 隐藏验收测试未过",
      sender_agent_id: "a-lead-01",
    },
    message_count: 24,
    live: true,
  },
  {
    room_id: "!room-core-dm:local",
    kind: "leader_dm",
    issue_id: ISSUE_ID,
    team_id: "t-0001",
    repository_id: REPO_API,
    repository_name: "saleor-core",
    members: [
      { agent_id: "a-org-lead", name: "manager · default", role: "organization_leader" },
      { agent_id: "a-lead-01", name: "leader · core", role: "repository_leader" },
    ],
    last_message: {
      at: "2026-08-11T15:30:00Z",
      kind: "governance",
      subject: "治理决策 ready: 门禁全绿放行合并",
      sender_agent_id: "a-org-lead",
    },
    message_count: 9,
    live: true,
  },
  {
    room_id: "!room-dashboard-team:local",
    kind: "team_room",
    issue_id: ISSUE_ID,
    team_id: "t-0002",
    repository_id: REPO_WEB,
    repository_name: "saleor-dashboard",
    members: [
      { agent_id: "a-lead-02", name: "leader · dashboard", role: "repository_leader" },
      { agent_id: "a-work-03", name: "worker · 8a1c22de", role: "worker" },
    ],
    last_message: {
      at: "2026-08-11T12:14:00Z",
      kind: "assignment",
      subject: "任务指派：dashboard 订单详情展示修改原因",
      sender_agent_id: "a-lead-02",
    },
    message_count: 6,
    live: false,
  },
  {
    room_id: "!room-dashboard-dm:local",
    kind: "leader_dm",
    issue_id: ISSUE_ID,
    team_id: "t-0002",
    repository_id: REPO_WEB,
    repository_name: "saleor-dashboard",
    members: [
      { agent_id: "a-org-lead", name: "manager · default", role: "organization_leader" },
      { agent_id: "a-lead-02", name: "leader · dashboard", role: "repository_leader" },
    ],
    last_message: null,
    message_count: 0,
    live: false,
  },
];

/** §5.2 四值全覆盖：message（真实气泡）+ governance / gate / runner（系统条目，无头像）。
 *  治理决策按 Q4 方案 A **只投进 leaderDM 流**，teamRoom 流不含 governance。 */
export const leaderDmStreamFixture: RoomStreamPage = {
  next_cursor: null,
  items: [
    {
      at: "2026-08-11T12:04:12Z",
      source: "message",
      room_id: "!room-core-dm:local",
      message: {
        id: "m-0001",
        kind: "status_report",
        subject: "第 2 轮候选已就绪",
        body: "core 的 price_override_reason 已落库并通过本仓单测，等待发布门禁。",
        sender_agent_id: "a-lead-01",
        sender_name: "leader · core",
        recipient_agent_id: "a-org-lead",
        recipient_name: "manager · default",
        repository_id: REPO_API,
        task_id: null,
        status: "delivered",
        event_id: null,
        correlation_id: null,
        created_at: "2026-08-11T12:04:12Z",
        direction: "inbound",
        room_id: "!room-core-dm:local",
      },
      text: null,
      repository_id: REPO_API,
      task_id: null,
      payload_ref: "message:m-0001",
    },
    {
      at: "2026-08-11T12:09:30Z",
      source: "runner",
      room_id: "!room-core-dm:local",
      message: null,
      text: "Runner 执行完成：pytest 42 passed",
      repository_id: REPO_API,
      task_id: "task-0007",
      payload_ref: "runner-event:re-0007",
    },
    {
      at: "2026-08-11T12:14:37Z",
      source: "gate",
      room_id: "!room-core-dm:local",
      message: null,
      text: "SCM 门禁：saleor#19466 CI 全绿，等待人工放行",
      repository_id: REPO_API,
      task_id: null,
      payload_ref: "gate:pr-19466",
    },
    {
      at: "2026-08-11T15:30:00Z",
      source: "governance",
      room_id: "!room-core-dm:local",
      message: null,
      text: "治理决策 ready: 门禁全绿放行合并",
      repository_id: REPO_API,
      task_id: null,
      payload_ref: "governance-decision:acd9f082",
    },
  ],
};

/** teamRoom 流：只有真实消息 + runner 投影，**不含 governance**（Q4 方案 A）。 */
export const teamRoomStreamFixture: RoomStreamPage = {
  next_cursor: null,
  items: [
    {
      at: "2026-08-11T12:12:00Z",
      source: "message",
      room_id: "!room-core-team:local",
      message: {
        id: "m-0101",
        kind: "assignment",
        subject: "任务指派：交付 core 价格原因字段",
        body: "按已冻结的工程契约实现本仓库范围，验收标准见任务卡。",
        sender_agent_id: "a-lead-01",
        sender_name: "leader · core",
        recipient_agent_id: "a-work-01",
        recipient_name: "worker · 597869c4",
        repository_id: REPO_API,
        task_id: "task-0007",
        status: "delivered",
        event_id: null,
        correlation_id: null,
        created_at: "2026-08-11T12:12:00Z",
        direction: "outbound",
        room_id: "!room-core-team:local",
      },
      text: null,
      repository_id: REPO_API,
      task_id: "task-0007",
      payload_ref: "message:m-0101",
    },
    {
      at: "2026-08-11T14:31:00Z",
      source: "message",
      room_id: "!room-core-team:local",
      message: {
        id: "m-0102",
        kind: "assignment",
        subject: "返工指派：修复 core 的失败候选",
        body: "隐藏验收测试 test_price_reason_audit 未过，按证据修正后重新提交。",
        sender_agent_id: "a-lead-01",
        sender_name: "leader · core",
        recipient_agent_id: "a-work-01",
        recipient_name: "worker · 597869c4",
        repository_id: REPO_API,
        task_id: "task-0009",
        status: "delivered",
        event_id: null,
        correlation_id: null,
        created_at: "2026-08-11T14:31:00Z",
        direction: "outbound",
        room_id: "!room-core-team:local",
      },
      text: null,
      repository_id: REPO_API,
      task_id: "task-0009",
      payload_ref: "message:m-0102",
    },
    {
      at: "2026-08-11T14:33:20Z",
      source: "runner",
      room_id: "!room-core-team:local",
      message: null,
      text: "Runner 启动返工任务 task-0009",
      repository_id: REPO_API,
      task_id: "task-0009",
      payload_ref: "runner-event:re-0009",
    },
  ],
};

/** 静默房间的历史流：live=false 不代表空，打开即完整历史（原型 `#v-detail` 的
 *  「静默房间 → 打开即完整历史」）。同为 teamRoom，同样不含 governance。 */
export const dashboardTeamStreamFixture: RoomStreamPage = {
  next_cursor: null,
  items: [
    {
      at: "2026-08-11T12:14:00Z",
      source: "message",
      room_id: "!room-dashboard-team:local",
      message: {
        id: "m-0201",
        kind: "assignment",
        subject: "任务指派：dashboard 订单详情展示修改原因",
        body: "订单详情页新增「价格修改原因」展示项，字段随 core 的 GraphQL 类型同步。",
        sender_agent_id: "a-lead-02",
        sender_name: "leader · dashboard",
        recipient_agent_id: "a-work-03",
        recipient_name: "worker · 8a1c22de",
        repository_id: REPO_WEB,
        task_id: "task-0011",
        status: "delivered",
        event_id: null,
        correlation_id: null,
        created_at: "2026-08-11T12:14:00Z",
        direction: "outbound",
        room_id: "!room-dashboard-team:local",
      },
      text: null,
      repository_id: REPO_WEB,
      task_id: "task-0011",
      payload_ref: "message:m-0201",
    },
    {
      at: "2026-08-11T12:41:10Z",
      source: "gate",
      room_id: "!room-dashboard-team:local",
      message: null,
      text: "SCM 门禁：dashboard#887 已合并",
      repository_id: REPO_WEB,
      task_id: null,
      payload_ref: "gate:pr-887",
    },
  ],
};

/** 按 room_id 取流。**不要按 kind 取**——那会让空房间显示别的房间的消息。
 *  未收录的房间返回空流，与 §5.1「空房间不装填占位消息」一致。 */
export const roomStreamFixtures: Record<string, RoomStreamPage> = {
  "!room-core-team:local": teamRoomStreamFixture,
  "!room-core-dm:local": leaderDmStreamFixture,
  "!room-dashboard-team:local": dashboardTeamStreamFixture,
  "!room-dashboard-dm:local": { items: [], next_cursor: null },
};

export const repositoryPlanFixture: RepositoryPlan = {
  issue_id: ISSUE_ID,
  repository_id: REPO_API,
  plan_version: 2,
  dag: {
    nodes: [
      { repository_id: REPO_API, name: "saleor-core", batch_index: 0, is_focus: true },
      { repository_id: REPO_WEB, name: "saleor-dashboard", batch_index: 1, is_focus: false },
      { repository_id: REPO_DOCS, name: "saleor-docs", batch_index: 1, is_focus: false },
    ],
    edges: [
      { from_repository_id: REPO_API, to_repository_id: REPO_WEB },
      { from_repository_id: REPO_API, to_repository_id: REPO_DOCS },
    ],
    granularity: "repository",
    edge_source: "task_dag.depends_on",
  },
  execution_batches: [["saleor-core"], ["saleor-dashboard", "saleor-docs"]],
  spec: {
    specification_id: "s-0001",
    kind: "repository",
    status: "frozen",
    revision: 2,
    goal: "订单模型新增 price_override_reason 字段与审计记录；GraphQL 订单类型暴露该字段；迁移保持向后兼容。",
    acceptance: [
      "结账写入订单时 price_override_reason 非空即持久化",
      "GraphQL 订单类型暴露 price_override_reason",
      "test_price_reason_audit 通过",
    ],
    allowed_paths: ["saleor/order/**", "graphql/order/**", "tests/**"],
    forbidden_paths: ["payments/**"],
    tests: ["pytest tests/order"],
  },
  engineering_contract: null,
};
