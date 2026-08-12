/** issue 详情 / 房间 replay 夹具。
 *
 *  **类型已全部迁到契约层** `api/contract.ts`（§3 / §5.1 / §5.2 / §5.4）——形状于
 *  2026-08-11 随 CONS-33 冻结，本文件只保留夹具数据，不再另抄一份字段表。
 *
 *  红线：state / phase / phase_note / runtime_status / live 均由读模型派生，只渲染不映射。 */
import type {
  DecisionsResponse,
  DeliveryAggregate,
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
  // 与 data/issues.ts 列表夹具同一派生同一结论（§3 详情 = §2 全字段之上追加）
  operational_status: "paused",
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
  // 契约 v0.4 §3.3 两标量，恒存在。与本 issue 的发现夹具默认形态（done）一致——
  // 这一单已经跑完整条发现链，计划快照就是它产出的。
  discovery_step: 4,
  discovery_state: "done",
};

/** 自检形态：**发现走完但尚未物化**的同一个 issue（`?issue=draft`）。
 *
 *  物化之前拓扑是空的——这正是本批开工前实走撞到的洞：`repositories` 为空 →
 *  计划 DAG 面板取不到锚点 → 恒显「尚未确定范围」，尽管计划已经在快照里。本形态
 *  同时是三块工作各自的自检屏：
 *   - 锚点回退：`repositories` 空，锚点只能走发现候选块那条回退路；
 *   - C-3 物化按钮：`rounds` 空 = 尚未物化，按钮此时才该出现；
 *   - C-4 着色：未物化 → 无轮次 → 无交付聚合 → 节点维持结构三视觉，不着执行态色。
 *
 *  与 `?discovery=done`（默认发现形态）同一个 issue 世界：那份夹具的 integration
 *  计数就是确认弹窗里要显示的 N。 */
export const issueDetailDraftFixture: IssueDetailView = {
  ...issueDetailFixture,
  phase: "plan",
  // 读模型派生，夹具照抄 live 上同形态 issue 的原文，不自造措辞
  phase_note: "计划 v1 待物化",
  operational_status: null,
  execution_mode: null,
  round_count: 0,
  active_round_id: null,
  latest_round_id: null,
  pending_decision_count: 0,
  repository_count: 0,
  team_count: 0,
  rounds: [],
  repositories: [],
  teams: [],
  human_grants: [],
  required_checkpoints: [],
  discovery_step: 4,
  discovery_state: "done",
};

/** 「本轮尚无快照」形态的共用底稿（A-4）。
 *
 *  形状逐字取自 8100 只读 GET（2026-08-12）：轮次行与两条仓库关联已落库，但该轮的
 *  `plan_version` / `created_at` / `updated_at` 三个字段都还是 null，任务 0、
 *  房间 0、聚合的 `change_set` / `matrix_room_id` 也全空。
 *
 *  契约 §3 把 `plan_version` 写成 `number`、`updated_at` 写成 `string`（均非空），
 *  **live 与契约在此处不符**：这正是 A-4 的病灶——`dayLabel(round.updated_at)` 对
 *  null 调 `.match` 抛 TypeError，一条数据把整个 issue 详情页打成白屏，连「重试
 *  物化」都够不着。下面两个形态都是那口反证井：修复前打开任一个必白屏。 */
const noSnapshotRoundBase = {
  ...issueDetailFixture,
  phase: "execute" as const,
  // 读模型派生，照抄 live 原文
  phase_note: "第 1/1 批执行中",
  operational_status: "active" as const,
  execution_mode: "auto" as const,
  round_count: 1,
  pending_decision_count: 0,
  repository_count: 2,
  team_count: 2,
  human_grants: [],
  required_checkpoints: [],
  discovery_step: 4 as const,
  discovery_state: "done" as const,
};

/** 自检形态①：**物化成功、尚未开工**的干净轮次（`?issue=freshround`）。
 *  活标本 `35e66beb-cd03-5aa5-83c8-00a3db31d9c7` / 轮次 `53ff1fd8…`。
 *  这是**常态**——首个 PlanSnapshot 落库之前每一轮都会经过这个形状。 */
export const issueDetailFreshRoundFixture: IssueDetailView = {
  ...noSnapshotRoundBase,
  title: "结算页支持订单备注：下单时允许买家填写备注，账单明细透出便于对账",
  requirement_text:
    "结算页支持订单备注：checkout 下单时允许买家填写备注并随订单提交，billing 在账单明细中透出该订单备注便于对账客服核查。",
  active_round_id: "53ff1fd8-21ea-44ea-a43a-13dde87f6e25",
  latest_round_id: "53ff1fd8-21ea-44ea-a43a-13dde87f6e25",
  rounds: [
    {
      round_id: "53ff1fd8-21ea-44ea-a43a-13dde87f6e25",
      phase: "execute",
      status: "in_progress",
      plan_version: null,
      created_at: null,
      updated_at: null,
    },
  ],
  repositories: [
    { repository_id: REPO_API, name: "saleor-core", team_id: "t-0001", role_in_issue: null },
    { repository_id: REPO_WEB, name: "saleor-dashboard", team_id: "t-0002", role_in_issue: null },
  ],
  teams: [
    { team_id: "t-0001", agentteams_team_name: "rm-team-a1b2c3", repository_id: REPO_API, runtime_status: "pending" },
    { team_id: "t-0002", agentteams_team_name: "rm-team-d4e5f6", repository_id: REPO_WEB, runtime_status: "pending" },
  ],
};

/** 自检形态②：**物化半途中断**留下的轮次（`?issue=halfround`）。
 *  活标本 `5c1b3567-42be-588d-b475-ab9cb0e03689` / 轮次 `8d8a3370…`。
 *
 *  **本形态与 `freshround` 的读模型投影逐字段一致**（除 round_id 与需求文本）——
 *  `rounds[0]`、聚合的 `plan`/`tasks`/`change_set`/`matrix_room_id`、`rooms` 全部同形，
 *  8100 上两个活标本对拉过。这不是夹具偷懒，**这就是结论**：界面拿不到区分
 *  「刚物化」与「中断」的任何字段，所以两个形态必须渲染出**同一句**诚实措辞
 *  （「本轮尚无快照」）。哪天服务端补出区分字段，先改这两个夹具让它们分叉，
 *  再谈分两句话说。 */
export const issueDetailHalfRoundFixture: IssueDetailView = {
  ...noSnapshotRoundBase,
  title: "结算页支持货到付款：新增支付方式选项，账单标注待收款并纳入对账单",
  requirement_text:
    "结算页支持货到付款：checkout 结算页增加货到付款支付方式选项并在下单时标记支付方式，billing 账单生成时对货到付款订单标注待收款状态并纳入对账单。",
  active_round_id: "8d8a3370-a24d-42fa-b2e3-6fb011189dcd",
  latest_round_id: "8d8a3370-a24d-42fa-b2e3-6fb011189dcd",
  rounds: [
    {
      round_id: "8d8a3370-a24d-42fa-b2e3-6fb011189dcd",
      phase: "execute",
      status: "in_progress",
      plan_version: null,
      created_at: null,
      updated_at: null,
    },
  ],
  repositories: [
    { repository_id: REPO_API, name: "saleor-core", team_id: "t-0001", role_in_issue: null },
    { repository_id: REPO_WEB, name: "saleor-dashboard", team_id: "t-0002", role_in_issue: null },
  ],
  teams: [
    { team_id: "t-0001", agentteams_team_name: "rm-team-a1b2c3", repository_id: REPO_API, runtime_status: "pending" },
    { team_id: "t-0002", agentteams_team_name: "rm-team-d4e5f6", repository_id: REPO_WEB, runtime_status: "pending" },
  ],
};

/** `?issue=<name>` 的取值表（自检开关，与 `?discovery=<name>` 同款；live 下不参与取数）。 */
export const issueDetailFixtures: Record<string, IssueDetailView> = {
  default: issueDetailFixture,
  draft: issueDetailDraftFixture,
  freshround: issueDetailFreshRoundFixture,
  halfround: issueDetailHalfRoundFixture,
};

export const ISSUE_DETAIL_FIXTURE_DEFAULT = "default";

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
      { agent_id: "a-lead-01", name: "rm-leader-core", role: "repository_leader" },
      { agent_id: "a-work-01", name: "rm-worker-597869c4", role: "worker" },
      { agent_id: "a-work-02", name: "rm-worker-b6b2f051", role: "worker" },
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
      { agent_id: "a-org-lead", name: "rm-manager", role: "organization_leader" },
      { agent_id: "a-lead-01", name: "rm-leader-core", role: "repository_leader" },
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
      { agent_id: "a-lead-02", name: "rm-leader-dashboard", role: "repository_leader" },
      { agent_id: "a-work-03", name: "rm-worker-8a1c22de", role: "worker" },
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
      { agent_id: "a-org-lead", name: "rm-manager", role: "organization_leader" },
      { agent_id: "a-lead-02", name: "rm-leader-dashboard", role: "repository_leader" },
    ],
    last_message: null,
    message_count: 0,
    live: false,
  },
];

/** 每个 `?issue=<name>` 形态各自的房间清单，与 `issueDetailFixtures` 同名对齐。
 *  `draft`（未物化：没有拓扑就没有团队）与 `freshround` / `halfround`（拓扑建了、
 *  房间还没跟上，8100 两个活标本实测 `{"rooms": []}`）都是 0 房间，但**不是同一件事**——
 *  所以逐条写死，不用一条规则从拓扑倒推。 */
export const issueRoomsFixtures: Record<string, RoomListItemView[]> = {
  default: roomsFixture,
  draft: [],
  freshround: [],
  halfround: [],
};

/** §5.2 四值全覆盖：message（真实气泡）+ governance / gate / runner（系统条目，无头像）。
 *  治理决策按 Q4 方案 A **只投进 leaderDM 流**，teamRoom 流不含 governance。 */
const leaderDmStreamFixture: RoomStreamPage = {
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
        sender_name: "rm-leader-core",
        recipient_agent_id: "a-org-lead",
        recipient_name: "rm-manager",
        repository_id: REPO_API,
        task_id: null,
        status: "delivered",
        event_id: null,
        correlation_id: null,
        created_at: "2026-08-11T12:04:12Z",
        direction: "leader_to_worker",
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
const teamRoomStreamFixture: RoomStreamPage = {
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
        sender_name: "rm-leader-core",
        recipient_agent_id: "a-work-01",
        recipient_name: "rm-worker-597869c4",
        repository_id: REPO_API,
        task_id: "task-0007",
        status: "delivered",
        event_id: null,
        correlation_id: null,
        created_at: "2026-08-11T12:12:00Z",
        direction: "leader_to_worker",
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
        sender_name: "rm-leader-core",
        recipient_agent_id: "a-work-01",
        recipient_name: "rm-worker-597869c4",
        repository_id: REPO_API,
        task_id: "task-0009",
        status: "delivered",
        event_id: null,
        correlation_id: null,
        created_at: "2026-08-11T14:31:00Z",
        direction: "leader_to_worker",
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
const dashboardTeamStreamFixture: RoomStreamPage = {
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
        sender_name: "rm-leader-dashboard",
        recipient_agent_id: "a-work-03",
        recipient_name: "rm-worker-8a1c22de",
        repository_id: REPO_WEB,
        task_id: "task-0011",
        status: "delivered",
        event_id: null,
        correlation_id: null,
        created_at: "2026-08-11T12:14:00Z",
        direction: "leader_to_worker",
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

// ══════════ 当前轮次的交付聚合与决策夹（v0.1 §3 / §4.3，环境窗与决策夹消费） ══════════

/** 聚合与决策夹由本文件自产，id 与房间/计划夹具同源，replay 世界自洽；只保留两个
 *  消费面真正要的部分：`repositoryEnvFromAggregate` 要 change_set / diffs /
 *  validation_snapshot / plan.merge_order，`approvalFromContract` 要 change_set 与仓库名。 */
const ROUND_ID = "2ebf564b-3bf2-5af1-ae24-3ccc4dd9d721";
const CHANGE_SET_ID = "cc84f1d0-51be-4b7e-9d02-88a3c67e2042";
const BASE_SHA = "d4c8b21a7e90f5d36b18a04c92e7f6531c80ee55";
const HEAD_API = "8825f6bb9c31d4a07e5f2b6d8a19c3e4f701aa42";
const HEAD_WEB = "6f21d3a8b90c47e12d5a8f3b6c94e07d1b28cc17";
const HEAD_DOCS = "9e04d5f127c8b3a6e0f49d21c75b8ae3f612dd90";

export const deliveryAggregateFixture: DeliveryAggregate = {
  delivery_id: ROUND_ID,
  project: {
    project_id: ISSUE_ID,
    project_key: null,
    title: issueDetailFixture.title,
    requirement_text: issueDetailFixture.requirement_text,
    created_at: issueDetailFixture.opened_at,
  },
  contract: issueDetailFixture.contract,
  repositories: [
    { repository_id: REPO_API, name: "saleor-core", evidence: null },
    { repository_id: REPO_WEB, name: "saleor-dashboard", evidence: null },
    { repository_id: REPO_DOCS, name: "saleor-docs", evidence: null },
  ],
  plan: {
    plan_version: 2,
    status: "in_progress",
    current_batch_index: 1,
    // 与 repositoryPlanFixture.execution_batches 同一份计划，两处必须一致
    execution_batches: [["saleor-core"], ["saleor-dashboard", "saleor-docs"]],
    merge_order: [REPO_API, REPO_WEB, REPO_DOCS],
  },
  /** C-4 着色的数据源。三仓三态**有意各不相同**：只摆一屏全绿等于没测——
   *  真出事时看的正是琥珀（在跑 / 修复中）与灰（等前置）那几个节点。
   *  `display_status` 是读模型按 §5.1 算好的，夹具照抄字面值，不在这里重算一遍。 */
  tasks: [
    {
      task_id: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a002",
      task_key: null,
      repository_id: REPO_API,
      title: "价格修改原因：模型、审计与 GraphQL 暴露",
      backend_status: "succeeded",
      display_status: "succeeded",
      agent: "rm-worker-597869c4",
      attempt: 1,
      depends_on: [],
      result_summary: "PR 19466：迁移 + 必填校验 + 审计字段，隐藏验收 9/9",
      // A-18 对照组：跑过、也跑绿了。默认形态里没有任何未验证标记，这样
      // `?tasks=unverified` 那一屏出现的琥珀标记才证明得了东西。
      evidence: {
        verified: true,
        blockers: [],
        summary_text: "迁移 + 必填校验 + 审计字段已完成，隐藏验收 9/9 全绿。",
        test_command: "pytest -q",
        test_results: [{ command: "pytest -q", exit_code: 0, summary: "412 passed" }],
        artifact_count: 1,
      },
      repair_timeline: [],
      escalated_to_human: false,
      last_dispatched_at: "2026-08-11T09:12:00Z",
    },
    {
      // §5.2：in_progress 且存在未终态 rework 链 → 展示 repairing（attempt 2）。
      // 这是 CI 失败后返工的形态，与 change_set 里那条 ci_failed 是同一件事的两个面。
      task_id: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a003",
      task_key: null,
      repository_id: REPO_WEB,
      title: "后台订单详情页展示价格修改原因",
      backend_status: "in_progress",
      display_status: "repairing",
      agent: "rm-worker-b6b2f051",
      attempt: 2,
      depends_on: ["b7d20c11-4e6f-4a83-9c01-6f2e8d94a002"],
      result_summary: null,
      // 还在跑，没有回报过——`evidence` 为 null 是「没有做过声明」，不是「未验证」
      evidence: null,
      repair_timeline: [{ at: "2026-08-11T11:40:00Z", what: "隐藏验收测试 3/9 失败，开返工任务" }],
      escalated_to_human: false,
      last_dispatched_at: "2026-08-11T11:41:00Z",
    },
    {
      task_id: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a005",
      task_key: null,
      repository_id: REPO_DOCS,
      title: "运营文档补价格修改原因章节",
      backend_status: "assigned",
      display_status: "pending",
      agent: null,
      attempt: 1,
      depends_on: ["b7d20c11-4e6f-4a83-9c01-6f2e8d94a002"],
      result_summary: null,
      // 尚未派工，同样是「没有做过声明」
      evidence: null,
      repair_timeline: [],
      escalated_to_human: false,
      last_dispatched_at: "2026-08-11T09:12:00Z",
    },
  ],
  change_set: {
    change_set_id: CHANGE_SET_ID,
    status: "delivering",
    merge_cursor: 0,
    repositories: [
      {
        repository_id: REPO_API,
        task_id: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a002",
        status: "ready_to_merge",
        gate_display: "open",
        pull_request_url: "https://github.com/saleor/saleor/pull/19466",
        pull_request_number: 19466,
        head_sha: HEAD_API,
        base_sha: BASE_SHA,
        branch_name: "repomesh/dlv-0042-core",
        depends_on: [],
        merge_order: 1,
        ci_checks: [
          { check_name: "单元测试", passed: true, summary: "412 通过" },
          { check_name: "隐藏验收测试", passed: true, summary: "9/9" },
          { check_name: "安全扫描", passed: true, summary: "0 高危" },
        ],
        required_checks: ["单元测试", "隐藏验收测试", "安全扫描"],
        required_approvals: 1,
        reviews: [{ reviewer: "security-reviewer", state: "approved", summary: "Security Reviewer 通过" }],
        // 与后端语义一致：缺 head-bound 治理决策时不放行，批准后才 allowed=true
        merge_gate: { allowed: false, reasons: ["head-bound governance decision is missing"] },
        merge_sha: null,
      },
      {
        repository_id: REPO_WEB,
        task_id: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a003",
        status: "ci_failed",
        gate_display: "blocked",
        pull_request_url: "https://github.com/saleor/saleor-dashboard/pull/6732",
        pull_request_number: 6732,
        head_sha: HEAD_WEB,
        base_sha: BASE_SHA,
        branch_name: "repomesh/dlv-0042-dashboard",
        depends_on: [REPO_API],
        merge_order: 2,
        ci_checks: [
          { check_name: "单元测试", passed: true, summary: "203 通过" },
          { check_name: "隐藏验收测试", passed: false, summary: "3/9 失败 · 空值渲染" },
        ],
        required_checks: ["单元测试", "隐藏验收测试"],
        required_approvals: 1,
        reviews: [],
        merge_gate: { allowed: false, reasons: ["required check 隐藏验收测试 失败", "缺少必需 Review"] },
        merge_sha: null,
      },
      {
        repository_id: REPO_DOCS,
        task_id: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a005",
        status: "pending",
        gate_display: "waiting",
        pull_request_url: null,
        pull_request_number: null,
        head_sha: HEAD_DOCS,
        base_sha: BASE_SHA,
        branch_name: "repomesh/dlv-0042-docs",
        depends_on: [REPO_API],
        merge_order: 3,
        ci_checks: [],
        required_checks: [],
        required_approvals: 0,
        reviews: [],
        merge_gate: { allowed: false, reasons: ["依赖仓库尚未合并"] },
        merge_sha: null,
      },
    ],
    governance_decisions: [],
    recovery_plans: [],
  },
  validation_snapshot: {
    id: "b0626c42-9f18-4d3a-8e57-2c9a1f0b7d64",
    status: "active",
    candidate_heads: { [REPO_API]: HEAD_API, [REPO_WEB]: HEAD_WEB, [REPO_DOCS]: HEAD_DOCS },
    environment_hash: "env-9f31c2d8",
    expires_at: "2026-08-11T18:00:00Z",
  },
  diffs: [
    {
      repository_id: REPO_API,
      run_id: "3bb524ce-8f01-4e2a-9d37-51c6a2e4b001",
      commit_sha: HEAD_API,
      changed_files: [
        "saleor/order/models.py",
        "saleor/graphql/order/types.py",
        "migrations/0042_price_override_reason.py",
        "tests/order/test_price_override_reason.py",
      ],
      // §6.3：Runner 未采集 ± 行数，恒 null——前端只列文件名
      diffstat: null,
    },
    {
      repository_id: REPO_WEB,
      run_id: "3bb524ce-8f01-4e2a-9d37-51c6a2e4b003",
      commit_sha: HEAD_WEB,
      changed_files: [
        "src/orders/components/OrderPriceOverrideNote.tsx",
        "src/graphql/types.generated.ts",
      ],
      diffstat: null,
    },
  ],
  cost: null,
  matrix_room_id: "!room-core-team:local",
  trace_id: null,
};

/** 本轮决策夹（§4.3）。approve 指向 REPO_API，与上面 change_set 里那条
 *  `merge_gate.allowed: false / 缺 head-bound 治理决策` 对应——批准它正是补上那一条。 */
/** C-4 的两条「拒绝派生」分支的自检形态（`?tasks=conflict`）。默认形态里每仓恰好
 *  一条任务，这两条分支永远走不到——而它们正是出事时才会被看到的那两个节点：
 *
 *   - saleor-dashboard 有**两条任务、展示态不一致**（父任务 failed + 返工任务
 *     running）。读模型没有给出「这个仓整体算什么态」这一事实，界面留白并写明
 *     几条任务不一致，不挑一条充数；
 *   - saleor-docs **本轮没有任务**：计划里有它，执行面还没有它。这与「等待」
 *     不是一回事，节点上如实写「本轮无任务」而不是涂成灰的 pending。 */
export const deliveryAggregateTaskConflictFixture: DeliveryAggregate = {
  ...deliveryAggregateFixture,
  tasks: [
    deliveryAggregateFixture.tasks[0],
    {
      ...deliveryAggregateFixture.tasks[1],
      backend_status: "failed",
      display_status: "failed",
      attempt: 1,
      repair_timeline: [],
    },
    {
      ...deliveryAggregateFixture.tasks[1],
      task_id: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a004",
      title: "后台订单详情页展示价格修改原因（返工）",
      backend_status: "in_progress",
      display_status: "running",
      attempt: 2,
    },
  ],
};

/** §8.7.4 重新派工入口的三种形态。**彼此只差派工相关的字段**，与
 *  `materialize_failed` / `materialized` 的兄弟夹具同一纪律：形态之间差得越少，
 *  界面上任何别的差异就越是真被这一个字段驱动的。
 *
 *  ① `stalled`——A-13 的活标本形状：两条任务停在 running/pending，派工时间是
 *     好几个小时前。入口出现，「上次派工」如实显示那个时间。
 *     **界面不因为这个时间久远就说「卡住了」**，那句话由人来说。 */
export const deliveryAggregateStalledFixture: DeliveryAggregate = {
  ...deliveryAggregateFixture,
  tasks: deliveryAggregateFixture.tasks.map((task, i) =>
    i === 0
      ? { ...task, backend_status: "in_progress", display_status: "running", result_summary: null }
      : task,
  ),
};

/** ② `never_dispatched`——第一个活标本的形状：任务行写下来了，派工消息从来没有。
 *     入口出现，并且弹窗明说「从未派工」——这不是缺数据，是这一轮压根没派出去。 */
export const deliveryAggregateNeverDispatchedFixture: DeliveryAggregate = {
  ...deliveryAggregateStalledFixture,
  tasks: deliveryAggregateStalledFixture.tasks.map((task) => ({
    ...task,
    last_dispatched_at: null,
  })),
};

/** ③ `rerun_needed`——2026-08-12 的第四个活标本：任务全部 succeeded，但结果里
 *     没有测试证据，交付因此一直被拒（`_candidates_for_batch` 在
 *     `_advance_if_ready` 里抛 ValueError，后台静默重试成环）。
 *     入口**出现**，但默认范围（只重发未完成的）在这里是空集——弹窗里那一档写
 *     着 0，人得显式选「连同已完成的一起重做」才动得了。这正是要的：写任务行的
 *     那一档不能是默认。 */
export const deliveryAggregateRerunNeededFixture: DeliveryAggregate = {
  ...deliveryAggregateFixture,
  tasks: deliveryAggregateFixture.tasks.map((task) => ({
    ...task,
    backend_status: "succeeded",
    display_status: "succeeded" as const,
    result_summary: "runner 完成，但没有测试结果",
  })),
};

/** ④ `all_settled`——真正无事可做的形状：全部 superseded（被新版计划替换）。
 *     入口**不出现**，卡上如实说明原因。用 superseded 而不是 succeeded 是有意的：
 *     succeeded 现在是可重做的（见 ③），而「决定不做」不是「做错了」。 */
export const deliveryAggregateSettledFixture: DeliveryAggregate = {
  ...deliveryAggregateFixture,
  tasks: deliveryAggregateFixture.tasks.map((task) => ({
    ...task,
    backend_status: "superseded",
    display_status: "failed" as const,
  })),
};

/** A-18 形态：**跑成功了、但 agent 说它一行都没执行过**。
 *
 *  形状照抄 live 任务 `6ba476ab`（run `d261dbb4`）：`succeeded` + 有 commit +
 *  `testResults: []` + `testCommand: null` + `artifacts: []`，而 agent 的 summary
 *  逐字写着它没跑过任何东西。原文这里节选自那一行的实际载荷（首尾两句一字未改），
 *  中段按篇幅省略，省略处显式标注——夹具可以短，但**不能替 agent 改口**。
 *
 *  三屏对照就在这份夹具里：默认形态 REPO_API 是验证过的干净成功（对照组），本形态
 *  同一条任务变成未验证（琥珀标记 + 授权单提示 + 证据面原话），另两条任务
 *  `evidence: null`（从没做过声明 → 什么标记都不出）。 */
const A18_SUMMARY = [
  "Implementation is complete. I could not execute anything to verify it — see below.",
  "",
  "## Blockers and gaps — read before accepting",
  "",
  "1. **Nothing was executed.** The sandbox refused every `python` invocation " +
    '("requires approval") and `git` as well. I have not run `scripts/run_tests.py` or the new ' +
    'tests, so "code compiles / existing tests pass" is reviewed-by-reading only, not verified. ' +
    "Please re-run before merging.",
  "",
  "2. **The task's vocabulary doesn't exist in this repo.** …（夹具节选，原文此处另有两段）",
  "",
  "3. **The \"matches billing's calculation\" test cannot be written honestly.** " +
    "`test_rate_consistency.py` contains it as an explicitly skipped test with the reason inline.",
  "",
  "4. **Allowed paths are `src/checkout/**`, which excludes `tests/`.** The new tests live in " +
    "`src/checkout/tests/` and are therefore *not* discovered by `scripts/run_tests.py`.",
].join("\n");

export const deliveryAggregateUnverifiedFixture: DeliveryAggregate = {
  ...deliveryAggregateFixture,
  tasks: [
    {
      ...deliveryAggregateFixture.tasks[0],
      evidence: {
        verified: false,
        // live 那条载荷没有结构化的 blockers 键（契约 6.12）——夹具照此留空，
        // 否则就是拿一个后端今天给不出的形状去验前端。
        blockers: [],
        summary_text: A18_SUMMARY,
        test_command: null,
        test_results: [],
        artifact_count: 0,
      },
    },
    deliveryAggregateFixture.tasks[1],
    deliveryAggregateFixture.tasks[2],
  ],
};

/** A-18 的另一半：Runner **确实**结构化声明了 blocker 时（契约 6.12 补齐后的形态）。
 *  与上一份的唯一差别是 blockers 非空——用来验「有声明才数条数」那条分支。 */
export const deliveryAggregateDeclaredBlockersFixture: DeliveryAggregate = {
  ...deliveryAggregateFixture,
  tasks: [
    {
      ...deliveryAggregateFixture.tasks[0],
      evidence: {
        verified: false,
        blockers: [
          "Nothing was executed. The sandbox refused every `python` invocation, so \"existing tests pass\" is reviewed-by-reading only. Please re-run before merging.",
          "The cross-repo consistency test is an explicitly skipped test with the reason inline; asserting against a figure this repo invented would be self-agreement, not verification.",
          "The new tests live in `src/checkout/tests/` and are not discovered by `scripts/run_tests.py`.",
        ],
        summary_text: A18_SUMMARY,
        test_command: null,
        test_results: [],
        artifact_count: 0,
      },
    },
    deliveryAggregateFixture.tasks[1],
    deliveryAggregateFixture.tasks[2],
  ],
};

/** A-18 第四面：**失败任务带着理由**。
 *
 *  形状照抄 live 两条失败行（plan 5b1cbfd1 / issue 74e9701e）：`commitSha: null`、
 *  `testResults` 空或红、理由在 `summary` 里。此前 parser 卡在「必须有非空 commitSha」，
 *  这两行的 evidence 一律投影成 null，界面只能说一句「failed」。
 *
 *  这份夹具同时验两件事：失败任务**显示理由**（赭红），且**不挂琥珀「未验证」标记**
 *  ——它的 `verified` 当然是 false，但失败已经是更响的那句话，双标记会稀释真正需要
 *  注意的那一类。第三条任务保留 default 的已验证成功，作对照。 */
export const deliveryAggregateFailedReasonFixture: DeliveryAggregate = {
  ...deliveryAggregateFixture,
  tasks: [
    deliveryAggregateFixture.tasks[0],
    {
      ...deliveryAggregateFixture.tasks[1],
      backend_status: "failed",
      display_status: "failed",
      attempt: 1,
      repair_timeline: [],
      evidence: {
        verified: false,
        blockers: [],
        // live 原文，逐字。它直接说明该改什么：把 tests/ 加进 allowed_paths。
        summary_text: "changed_path_denied: tests/test_discount.py",
        test_command: null,
        test_results: [],
        artifact_count: 0,
      },
    },
    {
      ...deliveryAggregateFixture.tasks[2],
      backend_status: "failed",
      display_status: "failed",
      evidence: {
        verified: false,
        blockers: [],
        // 另一条 live 失败行：命令**跑过**、退出码 1。「跑了」和「验证过」不是一回事。
        summary_text: "test_command_failed: python scripts/run_tests.py (exit code 1)",
        test_command: null,
        test_results: [
          { command: "python scripts/run_tests.py", exit_code: 1, summary: "" },
        ],
        artifact_count: 0,
      },
    },
  ],
};

export const deliveryAggregateFixtures: Record<string, DeliveryAggregate> = {
  default: deliveryAggregateFixture,
  conflict: deliveryAggregateTaskConflictFixture,
  stalled: deliveryAggregateStalledFixture,
  never_dispatched: deliveryAggregateNeverDispatchedFixture,
  rerun_needed: deliveryAggregateRerunNeededFixture,
  all_settled: deliveryAggregateSettledFixture,
  unverified: deliveryAggregateUnverifiedFixture,
  blockers: deliveryAggregateDeclaredBlockersFixture,
  failed_reason: deliveryAggregateFailedReasonFixture,
};

export const decisionsFixture: DecisionsResponse = {
  items: [
    {
      id: "dec-approve-core",
      kind: "approve",
      title: "批准 saleor-core 合并",
      body: "3 项必需检查全绿，独立 Review 通过。合并顺序第 1 位，后续两仓依赖此合并。",
      repository_id: REPO_API,
      head_sha: HEAD_API,
      created_at: "2026-08-11T16:10:00Z",
      actions: ["approve_merge", "view_evidence"],
    },
    {
      id: "dec-watch-dashboard",
      kind: "watch",
      title: "dashboard 修复循环进行中",
      body: "隐藏验收测试失败已定位（空值渲染），返工任务第 2 次尝试执行中；恢复计划未终态。",
      repository_id: REPO_WEB,
      head_sha: HEAD_WEB,
      created_at: "2026-08-11T16:06:00Z",
      actions: ["view_evidence"],
    },
  ],
};

/** 本轮事件时间线（v0.1 §4.1）。**轮次粒度**，故刻意混了三种归属：
 *  本仓（REPO_API）、别的仓（REPO_WEB）、以及 `repository_id: null` 的轮次级事实
 *  （计划生成）——环境窗是单仓作用域，这三类的取舍见 RoomView 的落位注释。
 *
 *  末条 `deny` 是**回放专属的治理拦截叙事**：契约 §6.6 规定 live 不应产出 deny，
 *  live 收到即渲染为违约警示（EventTimeline 的 `demo` 开关分流两种语义）。 */
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
