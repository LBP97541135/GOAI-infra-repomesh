/** issue 详情 / 房间 replay 夹具。
 *
 *  **类型已全部迁到契约层** `api/contract.ts`（§3 / §5.1 / §5.2 / §5.4）——形状于
 *  2026-08-11 随 CONS-33 冻结，本文件只保留夹具数据，不再另抄一份字段表。
 *
 *  红线：state / phase / phase_note / runtime_status / live 均由读模型派生，只渲染不映射。 */
import type {
  DeliveryEventsPage,
  IssueDetailView,
  RepositoryPlanView,
  RoomListItemView,
  RoomStreamPage,
} from "../api/contract";


// ══════════════ 夹具：沿用 #7f3d2a10（结账价格修改原因）三仓两轮场景 ══════════════

const ISSUE_ID = "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0042";
const ORG_ID = "0a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d";
const REPO_API = "b1c2d3e4-0001-4a2b-9c3d-4e5f6a7b8c01";
const REPO_WEB = "b1c2d3e4-0002-4a2b-9c3d-4e5f6a7b8c02";
const REPO_DOCS = "b1c2d3e4-0003-4a2b-9c3d-4e5f6a7b8c03";

export const issueDetailFixture: IssueDetailView = {
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
export const roomsFixture: RoomListItemView[] = [
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

export const repositoryPlanFixture: RepositoryPlanView = {
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

/** 本轮事件时间线（v0.1 §4.1）。**轮次粒度**，故刻意混了三种归属：
 *  本仓（REPO_API）、别的仓（REPO_WEB）、以及 `repository_id: null` 的轮次级事实
 *  （计划生成）——环境窗是单仓作用域，这三类的取舍见 RoomView 的落位注释。
 *
 *  末条 `deny` 是**回放专属的治理拦截叙事**：契约 §6.6 规定 live 不应产出 deny，
 *  live 收到即渲染为违约警示（EventTimeline 的 `demo` 开关分流两种语义）。
 *
 *  放这里而不是 data/replay.ts：后者随 v1 退役整体删除，不给待删文件加新引用。 */
export const roundEventsFixture: DeliveryEventsPage = {
  items: [
    { at: "2026-08-11T09:12:04Z", kind: "plan", text: "计划 v1 已生成 · 3 仓 2 批次", task_id: null, repository_id: null, payload_ref: "plan-snapshot:1" },
    { at: "2026-08-11T09:14:22Z", kind: "matrix", text: "task_assignment: 交付 core 价格原因字段", task_id: "t-api-1", repository_id: REPO_API, payload_ref: "message:1" },
    { at: "2026-08-11T09:15:01Z", kind: "runner", text: "runner.started · saleor-core", task_id: "t-api-1", repository_id: REPO_API, payload_ref: "run:1" },
    { at: "2026-08-11T09:31:47Z", kind: "runner", text: "runner.completed · saleor-core", task_id: "t-api-1", repository_id: REPO_API, payload_ref: "run:2" },
    { at: "2026-08-11T09:33:10Z", kind: "gate", text: "check_run.completed · pytest 通过", task_id: "t-api-1", repository_id: REPO_API, payload_ref: "check:1" },
    { at: "2026-08-11T09:41:52Z", kind: "matrix", text: "task_assignment: 后台订单详情页展示原因", task_id: "t-web-1", repository_id: REPO_WEB, payload_ref: "message:2" },
    { at: "2026-08-11T09:58:33Z", kind: "gate", text: "check_run.failed · dashboard 单测未过", task_id: "t-web-1", repository_id: REPO_WEB, payload_ref: "check:2" },
    { at: "2026-08-11T10:02:15Z", kind: "deny", text: "治理拦截：未经批准的直接合并请求已驳回", task_id: null, repository_id: REPO_API, payload_ref: "deny:1" },
  ],
  // 夹具即全量，没有第二页——不给一个点了没反应的「加载后续」
  next_cursor: null,
};
