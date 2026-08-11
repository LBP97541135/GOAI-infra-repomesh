/** Replay 夹具 —— 场景取自产品简报 §9 Saleor 四仓回放案例（DLV-0042）。
 *  数据形状 = 交付读模型契约 v0.1（src/api/contract.ts）；契约未覆盖的演示叙事
 *  （对话流、clarify 决策、±行数、成本等）放在 PresentationOverlay，live 模式无此层。 */
import type {
  ChangeSetView,
  DecisionsResponse,
  DeliveryAggregate,
  DeliveryEventsPage,
  DeliveryListResponse,
  DeliveryMessagesPage,
  DeliveryTaskView,
} from "../api/contract";
import type { ChatMessage, Clarification, Decision, PresentationOverlay, RepoDiff } from "../types";

/* ------------------------------------------------------------------ 标识符 */

export const IDS = {
  project: "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0042",
  delivery: "e91b6c44-24aa-4d67-8b7a-2f41d99e1042",
  changeSet: "cc84f1d0-51be-4b7e-9d02-88a3c67e2042",
  repo: {
    core: "a1e4c1b2-93d0-4f6e-8a12-04b6c9d1e001",
    dashboard: "a1e4c1b2-93d0-4f6e-8a12-04b6c9d1e002",
    apps: "a1e4c1b2-93d0-4f6e-8a12-04b6c9d1e003",
    docs: "a1e4c1b2-93d0-4f6e-8a12-04b6c9d1e004",
  },
  task: {
    t1: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a001",
    t2: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a002",
    t3: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a003",
    t4: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a004",
    t5: "b7d20c11-4e6f-4a83-9c01-6f2e8d94a005",
  },
} as const;

/** head_sha 恒非空 = Runner 产出的候选 commit SHA（主脑裁决 2026-08-11，契约 e9851ba）；
 *  pending 仅表示 PR 未创建，候选 commit 在 ChangeSet 创建时已存在。 */
const HEAD = {
  core: "8825f6bb9c31d4a07e5f2b6d8a19c3e4f701aa42",
  dashboard: "6f21d3a8b90c47e12d5a8f3b6c94e07d1b28cc17",
  apps: "4b09e7c2d18f43a6b5c290d7e83f1a64c507bb93",
  docs: "9e04d5f127c8b3a6e0f49d21c75b8ae3f612dd90",
} as const;

const BASE = "d4c8b21a7e90f5d36b18a04c92e7f6531c80ee55";

const SUMMARY =
  "外部 App 修改结账商品价格时记录修改原因；原因保存到订单，通过 GraphQL 暴露，并在管理后台展示，用于调试和审计。";

/* -------------------------------------------------------------- 契约形状 */

export const listResponse: DeliveryListResponse = {
  projects: [
    {
      project_id: IDS.project,
      project_key: "PRJ-2026-0042",
      title: "Saleor Commerce",
      deliveries: [
        {
          delivery_id: IDS.delivery,
          title: "结账价格修改原因：记录、暴露并在后台展示",
          phase: "release",
          phase_note: "2 完成 · 1 修复中 · 1 待审批",
          pending_decision_count: 2,
          updated_at: "2026-08-09T16:11:42Z",
        },
        {
          delivery_id: "e91b6c44-24aa-4d67-8b7a-2f41d99e1017",
          title: "购物车库存提示优化",
          phase: "delivered",
          phase_note: "08-06",
          pending_decision_count: 0,
          updated_at: "2026-08-06T10:20:00Z",
        },
        {
          delivery_id: null,
          title: "订单导出增加税率列",
          phase: "contract",
          phase_note: "契约澄清中",
          pending_decision_count: 0,
          updated_at: "2026-08-10T09:05:00Z",
        },
      ],
    },
  ],
  next_cursor: null,
};

const tasks: DeliveryTaskView[] = [
  {
    task_id: IDS.task.t1,
    task_key: "T1",
    repository_id: IDS.repo.core,
    title: "数据模型 + 迁移 + GraphQL 字段",
    backend_status: "succeeded",
    display_status: "succeeded",
    agent: "claude-code",
    attempt: 1,
    depends_on: [],
    result_summary: "17 个新增测试通过 · commit 8825f6bb · 迁移含回滚脚本",
    repair_timeline: [],
    escalated_to_human: false,
  },
  {
    task_id: IDS.task.t2,
    task_key: "T2",
    repository_id: IDS.repo.core,
    title: "权限校验（MANAGE_ORDERS）",
    backend_status: "succeeded",
    display_status: "succeeded",
    agent: "claude-code",
    attempt: 1,
    depends_on: [IDS.task.t1],
    result_summary: "权限矩阵测试通过 · commit 3c91d02a",
    repair_timeline: [],
    escalated_to_human: false,
  },
  {
    task_id: IDS.task.t3,
    task_key: "T3",
    repository_id: IDS.repo.dashboard,
    title: "GraphQL 类型 + 订单详情展示",
    backend_status: "in_progress",
    display_status: "repairing",
    agent: "codex",
    attempt: 2,
    depends_on: [IDS.task.t1],
    result_summary: "隐藏验收测试失败：reason 为 null 时组件抛错。修复循环第 2 次。",
    repair_timeline: [
      { at: "2026-08-09T16:02:19Z", what: "QA Guardian 隐藏测试 3/9 失败：OrderPriceOverrideNote 空值渲染崩溃" },
      { at: "2026-08-09T16:05:57Z", what: "诊断：缺少 null guard；修复范围限定 src/orders/components/**" },
      { at: "2026-08-09T16:11:42Z", what: "第 2 次尝试执行中 · 再失败将由恢复计划升级人工" },
    ],
    escalated_to_human: false,
  },
  {
    task_id: IDS.task.t4,
    task_key: "T4",
    repository_id: IDS.repo.apps,
    title: "示例支付 App 传递修改原因",
    backend_status: "in_progress",
    display_status: "running",
    agent: "claude-code",
    attempt: 1,
    depends_on: [IDS.task.t1],
    result_summary: "Runner 执行中 · 已产出变更 2 文件，验收命令待运行",
    repair_timeline: [],
    escalated_to_human: false,
  },
  {
    task_id: IDS.task.t5,
    task_key: "T5",
    repository_id: IDS.repo.docs,
    title: "API 文档与示例同步",
    backend_status: "assigned",
    display_status: "pending",
    agent: "hermes",
    attempt: 1,
    depends_on: [IDS.task.t3, IDS.task.t4],
    result_summary: null,
    repair_timeline: [],
    escalated_to_human: false,
  },
];

const changeSet: ChangeSetView = {
  change_set_id: IDS.changeSet,
  status: "delivering",
  merge_cursor: 0,
  repositories: [
    {
      repository_id: IDS.repo.core,
      task_id: IDS.task.t2,
      status: "ready_to_merge",
      gate_display: "open",
      pull_request_url: "https://github.com/saleor/saleor/pull/19466",
      pull_request_number: 19466,
      head_sha: HEAD.core,
      base_sha: BASE,
      branch_name: "repomesh/dlv-0042-core",
      depends_on: [],
      merge_order: 1,
      ci_checks: [
        { check_name: "单元测试", passed: true, summary: "412 通过" },
        { check_name: "集成测试", passed: true, summary: "58 通过" },
        { check_name: "隐藏验收测试", passed: true, summary: "9/9" },
        { check_name: "安全扫描", passed: true, summary: "0 高危" },
      ],
      required_checks: ["单元测试", "集成测试", "隐藏验收测试", "安全扫描"],
      required_approvals: 1,
      reviews: [{ reviewer: "security-reviewer", state: "approved", summary: "Security Reviewer 通过" }],
      merge_gate: { allowed: true, reasons: [] },
      merge_sha: null,
    },
    {
      repository_id: IDS.repo.dashboard,
      task_id: IDS.task.t3,
      status: "ci_failed",
      gate_display: "blocked",
      pull_request_url: "https://github.com/saleor/saleor-dashboard/pull/6732",
      pull_request_number: 6732,
      head_sha: HEAD.dashboard,
      base_sha: BASE,
      branch_name: "repomesh/dlv-0042-dashboard",
      depends_on: [IDS.repo.core],
      merge_order: 2,
      ci_checks: [
        { check_name: "单元测试", passed: true, summary: "203 通过" },
        { check_name: "隐藏验收测试", passed: false, summary: "3/9 失败 · 空值渲染" },
        { check_name: "安全扫描", passed: true, summary: "0 高危" },
      ],
      required_checks: ["单元测试", "隐藏验收测试", "安全扫描"],
      required_approvals: 1,
      reviews: [],
      merge_gate: { allowed: false, reasons: ["required check 隐藏验收测试 失败", "缺少必需 Review"] },
      merge_sha: null,
    },
    {
      repository_id: IDS.repo.apps,
      task_id: IDS.task.t4,
      status: "ci_pending",
      gate_display: "running",
      pull_request_url: "https://github.com/saleor/apps/pull/2393",
      pull_request_number: 2393,
      head_sha: HEAD.apps,
      base_sha: BASE,
      branch_name: "repomesh/dlv-0042-apps",
      depends_on: [IDS.repo.core],
      merge_order: 2,
      ci_checks: [],
      required_checks: ["单元测试", "隐藏验收测试", "安全扫描"],
      required_approvals: 1,
      reviews: [],
      merge_gate: { allowed: false, reasons: ["CI 未完成"] },
      merge_sha: null,
    },
    {
      repository_id: IDS.repo.docs,
      task_id: IDS.task.t5,
      status: "pending",
      gate_display: "waiting",
      pull_request_url: null,
      pull_request_number: null,
      head_sha: HEAD.docs,
      base_sha: BASE,
      branch_name: "repomesh/dlv-0042-docs",
      depends_on: [IDS.repo.dashboard, IDS.repo.apps],
      merge_order: 3,
      ci_checks: [],
      required_checks: ["构建检查", "链接检查"],
      required_approvals: 1,
      reviews: [],
      merge_gate: { allowed: false, reasons: ["上游仓库未合并"] },
      merge_sha: null,
    },
  ],
  governance_decisions: [],
  recovery_plans: [
    {
      trigger: "hidden-acceptance-check failed (saleor-dashboard)",
      reason: "OrderPriceOverrideNote 空值渲染崩溃，返工任务 T3 第 2 次尝试进行中",
      actions: [],
    },
  ],
};

export const aggregate: DeliveryAggregate = {
  delivery_id: IDS.delivery,
  project: {
    project_id: IDS.project,
    project_key: "PRJ-2026-0042",
    title: "结账价格修改原因：记录、暴露并在后台展示",
    requirement_text: SUMMARY,
    created_at: "2026-08-09T14:02:00Z",
  },
  contract: {
    specification_id: "9d02f4e8-71cc-4b95-a3d6-0c85be71a042",
    version: 3,
    status: "frozen",
    goal: "允许外部 App 在结账时为价格修改附加原因；订单侧持久化并可审计。",
    acceptance: [
      "Checkout price override 携带 reason 字段，长度 ≤ 512，可为空但不可为纯空白",
      "订单详情 GraphQL 暴露 priceOverrideReason，受 MANAGE_ORDERS 权限保护",
      "管理后台订单详情页展示修改原因，空值时不渲染该区块",
      "已有结账与订单流程零回归（原有测试全部通过）",
    ],
    constraints: ["原因原样存储、原样展示，视为审计文本，不做本地化", "历史订单该字段返回 null，Dashboard 空值不渲染"],
    allowed_paths: ["saleor/graphql/**", "saleor/order/**", "src/orders/**", "docs/api/**"],
    forbidden_paths: ["**/settings/**", "**/migrations/manual/**"],
    tests: ["pytest saleor/order saleor/graphql/order", "npm test -- src/orders", "隐藏验收套件 9 用例（QA Guardian 独立执行）"],
    non_goals: null,
    release_rules: null,
  },
  repositories: [
    { repository_id: IDS.repo.core, name: "saleor-core", evidence: "依赖扫描：order 模块被 3 仓引用" },
    { repository_id: IDS.repo.dashboard, name: "saleor-dashboard", evidence: "引用 core GraphQL schema" },
    { repository_id: IDS.repo.apps, name: "saleor-apps", evidence: "调用 checkout mutation" },
    { repository_id: IDS.repo.docs, name: "saleor-docs", evidence: "文档引用 checkout API" },
  ],
  plan: {
    plan_version: 2,
    status: "in_progress",
    current_batch_index: 1,
    execution_batches: [["saleor-core"], ["saleor-dashboard", "saleor-apps"], ["saleor-docs"]],
    merge_order: [IDS.repo.core, IDS.repo.dashboard, IDS.repo.apps, IDS.repo.docs],
  },
  tasks,
  change_set: changeSet,
  validation_snapshot: {
    id: "snap-dlv0042-01",
    status: "active",
    candidate_heads: {
      [IDS.repo.core]: HEAD.core,
      [IDS.repo.dashboard]: HEAD.dashboard,
      [IDS.repo.apps]: HEAD.apps,
      [IDS.repo.docs]: HEAD.docs,
    },
    environment_hash: "env-9f31c2d8",
    expires_at: "2026-08-09T18:00:00Z",
  },
  diffs: [
    {
      repository_id: IDS.repo.core,
      run_id: "3bb524ce-8f01-4e2a-9d37-51c6a2e4b001",
      commit_sha: HEAD.core,
      changed_files: [
        "saleor/order/models.py",
        "saleor/graphql/order/types.py",
        "migrations/0042_price_override_reason.py",
        "tests/order/test_price_override_reason.py",
      ],
      diffstat: null,
    },
    {
      repository_id: IDS.repo.core,
      run_id: "3bb524ce-8f01-4e2a-9d37-51c6a2e4b002",
      commit_sha: "3c91d02a5b74e8f0c6d21a93b85e4f172d09cc61",
      changed_files: ["saleor/graphql/order/permissions.py", "tests/order/test_permissions.py"],
      diffstat: null,
    },
    {
      repository_id: IDS.repo.dashboard,
      run_id: "3bb524ce-8f01-4e2a-9d37-51c6a2e4b003",
      commit_sha: HEAD.dashboard,
      changed_files: [
        "src/orders/components/OrderPriceOverrideNote.tsx",
        "src/graphql/types.generated.ts",
        "src/orders/queries.ts",
      ],
      diffstat: null,
    },
  ],
  cost: null,
  matrix_room_id: "!dlv0042:matrix.repomesh.local",
  trace_id: null,
};

export const eventsPage: DeliveryEventsPage = {
  items: [
    { at: "2026-08-09T16:11:42Z", kind: "runner", text: "runner.accepted · T3 第 2 次尝试 · worktree wt-6f21", task_id: IDS.task.t3, repository_id: IDS.repo.dashboard, payload_ref: null },
    { at: "2026-08-09T16:08:03Z", kind: "matrix", text: "Leader → dashboard-worker：修复指令已送达（Matrix delivered）", task_id: IDS.task.t3, repository_id: IDS.repo.dashboard, payload_ref: null },
    { at: "2026-08-09T16:05:57Z", kind: "plan", text: "Repair Loop：生成修复任务包，范围限定 src/orders/components/**", task_id: IDS.task.t3, repository_id: IDS.repo.dashboard, payload_ref: null },
    { at: "2026-08-09T16:02:19Z", kind: "gate", text: "QA Guardian：隐藏验收测试 3/9 失败 → dashboard 门禁 BLOCKED", task_id: IDS.task.t3, repository_id: IDS.repo.dashboard, payload_ref: null },
    // 契约 §4.1：live 后端 v0.1 不产出 deny；此条仅回放叙事（治理拦截黄条）
    { at: "2026-08-09T15:58:44Z", kind: "deny", text: "治理：T3 尝试自行运行验收测试被权限层拒绝（自证不算数）", task_id: IDS.task.t3, repository_id: IDS.repo.dashboard, payload_ref: null },
    { at: "2026-08-09T15:52:10Z", kind: "runner", text: "runner.completed · T2 succeeded · commit 3c91d02a", task_id: IDS.task.t2, repository_id: IDS.repo.core, payload_ref: null },
    { at: "2026-08-09T15:47:31Z", kind: "runner", text: "runner.completed · T4 变更采集 2 文件（仅允许路径）", task_id: IDS.task.t4, repository_id: IDS.repo.apps, payload_ref: null },
    { at: "2026-08-09T15:40:12Z", kind: "gate", text: "Security Reviewer：saleor-core 扫描 0 高危，Review 通过", task_id: null, repository_id: IDS.repo.core, payload_ref: null },
    { at: "2026-08-09T15:31:26Z", kind: "runner", text: "runner.completed · T1 succeeded · 17 测试通过 · commit 8825f6bb", task_id: IDS.task.t1, repository_id: IDS.repo.core, payload_ref: null },
    { at: "2026-08-09T15:02:08Z", kind: "plan", text: "Task DAG v2 冻结：5 任务 · 3 层拓扑 · 合并顺序 core→dashboard/apps→docs", task_id: null, repository_id: null, payload_ref: null },
  ],
  next_cursor: null,
};

export const messagesPage: DeliveryMessagesPage = {
  items: [
    {
      kind: "task_dispatch",
      subject: "T3 修复任务包已发布",
      body: "隐藏验收测试 3/9 失败，修复范围限定 src/orders/components/**，任务包已发布到共享存储。",
      sender: "repomesh-pricing-leader",
      recipient: "dashboard-worker",
      status: "delivered",
      event_id: "$evt-repair-t3-r2",
      correlation_id: IDS.task.t3,
    },
    {
      kind: "task_dispatch",
      subject: "T4 任务包已发布",
      body: "示例支付 App 传递修改原因，验收命令由 Runner 受控执行。",
      sender: "repomesh-pricing-leader",
      recipient: "apps-worker",
      status: "delivered",
      event_id: "$evt-dispatch-t4",
      correlation_id: IDS.task.t4,
    },
  ],
  next_cursor: null,
};

export const decisionsResponse: DecisionsResponse = {
  items: [
    {
      id: "dec-approve-core",
      kind: "approve",
      title: "批准 saleor-core 合并",
      body: "4 项必需检查全绿，独立 Review 通过。合并顺序第 1 位，后续 3 仓依赖此合并。",
      repository_id: IDS.repo.core,
      head_sha: HEAD.core,
      created_at: "2026-08-09T16:10:00Z",
      actions: ["approve_merge", "view_evidence"],
    },
    {
      id: "dec-watch-dashboard",
      kind: "watch",
      title: "dashboard 修复循环进行中",
      body: "隐藏验收测试失败已定位（空值渲染），返工任务第 2 次尝试执行中；恢复计划未终态。",
      repository_id: IDS.repo.dashboard,
      head_sha: HEAD.dashboard,
      created_at: "2026-08-09T16:06:00Z",
      actions: ["view_evidence"],
    },
  ],
};

/* -------------------------------------------------------- 演示叙事覆盖层 */

export const clarifications: Clarification[] = [
  {
    q: "原因字段是否需要多语言？后台展示是否按操作者语言本地化？",
    a: "不需要。原样存储、原样展示，视为审计文本。",
    by: "Product Analyst → 王倩",
    at: "08-09 14:31",
  },
  {
    q: "历史订单没有该字段，GraphQL 返回 null 还是空串？",
    a: "返回 null，Dashboard 空值不渲染。",
    by: "Product Analyst → 王倩",
    at: "08-09 14:40",
  },
];

const chat: ChatMessage[] = [
  {
    id: "m1",
    author: "王倩",
    role: "HUMAN",
    time: "14:02",
    tone: "user",
    text: SUMMARY,
    attach: { label: "PRD", name: "checkout-price-override-reason.md", meta: "目标 · 验收标准 · 兼容约束 · 18KB" },
  },
  {
    id: "m2",
    author: "Product Analyst",
    role: "AGENT",
    time: "14:31",
    tone: "agent",
    text: "有 2 个关键歧义需要你确认，已按你的回答写入契约：",
    clarifications,
  },
  {
    id: "m3",
    author: "Project Manager",
    role: "AGENT",
    time: "14:55",
    tone: "agent",
    text: "契约已冻结（v3）。仓库发现完成，自动选仓只提供证据，范围由你确认。",
    artifact: "scope",
  },
  {
    id: "m4",
    author: "Project Manager",
    role: "AGENT",
    time: "15:02",
    tone: "agent",
    text: "任务 DAG 已生成，4 个 Worker 在隔离 Worktree 中执行，全部提交只写入 Worktree。",
    artifact: "dag",
  },
  { id: "m5", author: "QA Guardian", role: "AGENT", time: "16:02", tone: "qa", artifact: "fail" },
  { id: "m6", author: "Release Guardian", role: "AGENT", time: "16:10", tone: "agent", artifact: "approve" },
];

/** clarify 决策：契约 §6.5 无后端实体，仅回放模式演示 */
const clarifyDecision: Decision = {
  id: "demo-clarify-docs",
  kind: "clarify",
  urgency: "later",
  title: "澄清：docs 是否需要中文版",
  body: "saleor-docs 现有 API 页存在 zh 目录。契约未覆盖翻译范围，T5 默认只更新英文。",
  actions: ["只更新英文", "包含中文", "稍后回答"],
  actionKinds: null,
  repositoryId: IDS.repo.docs,
  headSha: null,
};

/** 契约 diffstat=null（§6.3）时的演示 ± 行数（live 模式只列文件名） */
const demoRepoDiffs: RepoDiff[] = [
  {
    id: "saleor-core",
    add: 412,
    del: 18,
    note: "8825f6bb · 3c91d02a",
    files: [
      { path: "saleor/order/models.py", add: 38, del: 2 },
      { path: "saleor/graphql/order/types.py", add: 54, del: 0 },
      { path: "migrations/0042_price_override_reason.py", add: 61, del: 0 },
      { path: "tests/（5 个文件）", add: 259, del: 16 },
    ],
  },
  {
    id: "saleor-dashboard",
    add: 186,
    del: 24,
    note: "修复中 · wt-6f21",
    files: [
      { path: "src/orders/…/OrderPriceOverrideNote.tsx", add: 92, del: 0 },
      { path: "src/graphql/types.generated.ts", add: 61, del: 24 },
      { path: "src/orders/queries.ts", add: 33, del: 0 },
    ],
  },
  {
    id: "saleor-apps",
    add: 48,
    del: 6,
    note: "执行中 · 变更采集中",
    files: [
      { path: "apps/payment-example/src/checkout.ts", add: 41, del: 6 },
      { path: "apps/payment-example/README.md", add: 7, del: 0 },
    ],
  },
  { id: "saleor-docs", add: 0, del: 0, note: "等待 T5 启动", files: [] },
];

export const overlay: PresentationOverlay = {
  deliveryLabel: "DLV-0042",
  runLabel: "RUN 2H09M",
  chat,
  extraDecisions: [clarifyDecision],
  nonGoals: ["不改动促销/折扣引擎", "不提供原因的批量编辑界面"],
  rollbackPlan: [
    "撤销预发部署（未执行——尚未部署）",
    "按合并逆序 revert：docs → apps/dashboard → core",
    "core 迁移执行补偿脚本 0042_price_override_reason_down.sql",
    "恢复到基线 tag delivery-0042-base，验证四仓一致性",
  ],
  repoDiffs: demoRepoDiffs,
  costLabel: "1.24M tok · ¥8.42 · 2h09m",
  matrixAlias: "#dlv-0042",
  approvalAuthority: "Release Guardian",
  mergeOrderLabel: "core → dashboard / apps → docs",
  stagingNote: "未部署 · 等 core 合并",
  envProcesses: ["repomesh-runner · poll /runner-tasks/next", "otlp-exporter → localhost:3001/v1/traces"],
};

/* 场景状态机（回放模式的 4 阶段推进）见 ./scenes.ts */
