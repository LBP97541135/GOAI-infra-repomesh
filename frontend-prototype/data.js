// PROTOTYPE — mock 数据，场景取自产品简报 §9 Saleor 四仓回放案例
window.MOCK = {
  delivery: {
    id: "DLV-0042",
    title: "结账价格修改原因：记录、暴露并在后台展示",
    summary:
      "外部 App 修改结账商品价格时记录修改原因；原因保存到订单，通过 GraphQL 暴露，并在管理后台展示，用于调试和审计。",
    requester: "王倩 · 产品经理",
    createdAt: "2026-08-09 14:02",
    runId: "9f2c1a4e-…-0001",
    stages: [
      { key: "contract", name: "交付契约", status: "done", note: "已冻结 v3" },
      { key: "plan", name: "跨仓计划", status: "done", note: "4 仓 · 5 任务" },
      { key: "execute", name: "执行", status: "running", note: "2 完成 · 1 修复中" },
      { key: "validate", name: "独立验证", status: "attention", note: "1 门禁受阻" },
      { key: "release", name: "发布", status: "waiting", note: "等待审批" },
    ],
  },

  contract: {
    goal: "允许外部 App 在结账时为价格修改附加原因；订单侧持久化并可审计。",
    acceptance: [
      "Checkout price override 携带 reason 字段，长度 ≤ 512，可为空但不可为纯空白",
      "订单详情 GraphQL 暴露 priceOverrideReason，受 MANAGE_ORDERS 权限保护",
      "管理后台订单详情页展示修改原因，空值时不渲染该区块",
      "已有结账与订单流程零回归（原有测试全部通过）",
    ],
    nonGoals: ["不改动促销/折扣引擎", "不提供原因的批量编辑界面"],
    scope: {
      repositories: ["saleor-core", "saleor-dashboard", "saleor-apps", "saleor-docs"],
      allowedPaths: ["saleor/graphql/**", "saleor/order/**", "src/orders/**", "docs/api/**"],
      forbiddenPaths: ["**/settings/**", "**/migrations/manual/**"],
    },
    gatesRequired: ["单元测试", "集成测试", "隐藏验收测试", "安全扫描", "预发冒烟"],
    release: { humanApproval: true, rollbackCondition: "预发订单创建成功率 < 99.5% 持续 10 分钟" },
  },

  clarifications: [
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
  ],

  repos: [
    { id: "saleor-core", lang: "Python", role: "后端 · 数据模型 / GraphQL", evidence: "依赖扫描：order 模块被 3 仓引用" },
    { id: "saleor-dashboard", lang: "TypeScript", role: "管理后台 · 订单详情展示", evidence: "引用 core GraphQL schema" },
    { id: "saleor-apps", lang: "TypeScript", role: "示例支付 App · 传递 reason", evidence: "调用 checkout mutation" },
    { id: "saleor-docs", lang: "MDX", role: "API 文档同步", evidence: "文档引用 checkout API" },
  ],

  // DAG：lane = repo 索引，col = 拓扑层
  tasks: [
    {
      id: "T1", repo: "saleor-core", col: 0, lane: 0,
      title: "数据模型 + 迁移 + GraphQL 字段",
      status: "succeeded", agent: "claude-code", attempt: 1,
      detail: "17 个新增测试通过 · commit 8825f6bb · 迁移含回滚脚本",
    },
    {
      id: "T2", repo: "saleor-core", col: 1, lane: 0,
      title: "权限校验（MANAGE_ORDERS）",
      status: "succeeded", agent: "claude-code", attempt: 1,
      detail: "权限矩阵测试通过 · commit 3c91d02a",
      deps: ["T1"],
    },
    {
      id: "T3", repo: "saleor-dashboard", col: 1, lane: 1,
      title: "GraphQL 类型 + 订单详情展示",
      status: "repairing", agent: "codex", attempt: 2,
      detail: "隐藏验收测试失败：reason 为 null 时组件抛错。修复循环第 2/3 次。",
      deps: ["T1"],
      repair: [
        { at: "16:02", what: "QA Guardian 隐藏测试 3/9 失败：OrderPriceOverrideNote 空值渲染崩溃" },
        { at: "16:05", what: "诊断：缺少 null guard；修复范围限定 src/orders/components/**" },
        { at: "16:11", what: "第 2 次尝试执行中 · 剩余重试 1 次，再失败升级人工" },
      ],
    },
    {
      id: "T4", repo: "saleor-apps", col: 1, lane: 2,
      title: "示例支付 App 传递修改原因",
      status: "running", agent: "claude-code", attempt: 1,
      detail: "Runner 执行中 · 已产出变更 2 文件，验收命令待运行",
      deps: ["T1"],
    },
    {
      id: "T5", repo: "saleor-docs", col: 2, lane: 3,
      title: "API 文档与示例同步",
      status: "pending", agent: "hermes", attempt: 0,
      detail: "等待 T3、T4 完成后启动",
      deps: ["T3", "T4"],
    },
  ],

  gates: [
    {
      repo: "saleor-core", state: "open",
      checks: [
        { name: "单元测试", s: "pass", note: "412 通过" },
        { name: "集成测试", s: "pass", note: "58 通过" },
        { name: "隐藏验收测试", s: "pass", note: "9/9" },
        { name: "安全扫描", s: "pass", note: "0 高危" },
        { name: "独立 Review", s: "pass", note: "Security Reviewer 通过" },
      ],
      pr: "saleor/saleor#19466 · 待合并（等审批）",
    },
    {
      repo: "saleor-dashboard", state: "blocked",
      checks: [
        { name: "单元测试", s: "pass", note: "203 通过" },
        { name: "隐藏验收测试", s: "fail", note: "3/9 失败 · 空值渲染" },
        { name: "安全扫描", s: "pass", note: "0 高危" },
        { name: "独立 Review", s: "wait", note: "等待修复" },
      ],
      pr: "saleor-dashboard#6732 · 草稿",
    },
    {
      repo: "saleor-apps", state: "running",
      checks: [
        { name: "单元测试", s: "run", note: "CI 运行中" },
        { name: "隐藏验收测试", s: "wait", note: "排队" },
        { name: "安全扫描", s: "wait", note: "排队" },
      ],
      pr: "saleor-apps#2393 · 草稿",
    },
    {
      repo: "saleor-docs", state: "waiting",
      checks: [
        { name: "构建检查", s: "wait", note: "等待 T5" },
        { name: "链接检查", s: "wait", note: "等待 T5" },
      ],
      pr: "未创建",
    },
  ],

  decisions: [
    {
      id: "D-1", kind: "approve", urgency: "now",
      title: "批准 saleor-core 合并",
      body: "5 项门禁全绿，独立 Review 通过。合并顺序第 1 位，后续 3 仓依赖此合并。",
      actions: ["批准合并", "查看证据"],
    },
    {
      id: "D-2", kind: "watch", urgency: "soon",
      title: "dashboard 修复循环 2/3",
      body: "隐藏验收测试第 1 次失败已定位（空值渲染）。若第 3 次仍失败，将按契约升级人工接管。",
      actions: ["查看失败详情", "立即升级人工"],
    },
    {
      id: "D-3", kind: "clarify", urgency: "later",
      title: "澄清：docs 是否需要中文版",
      body: "saleor-docs 现有 API 页存在 zh 目录。契约未覆盖翻译范围，T5 默认只更新英文。",
      actions: ["只更新英文", "包含中文", "稍后回答"],
    },
  ],

  events: [
    { at: "16:11:42", kind: "runner", text: "runner.accepted · T3 第 2 次尝试 · worktree wt-6f21" },
    { at: "16:08:03", kind: "matrix", text: "Leader → dashboard-worker：修复指令已送达（Matrix delivered）" },
    { at: "16:05:57", kind: "plan", text: "Repair Loop：生成修复任务包，范围限定 src/orders/components/**" },
    { at: "16:02:19", kind: "gate", text: "QA Guardian：隐藏验收测试 3/9 失败 → dashboard 门禁 BLOCKED" },
    { at: "15:58:44", kind: "deny", text: "治理：T3 尝试自行运行验收测试被权限层拒绝（自证不算数）" },
    { at: "15:52:10", kind: "runner", text: "runner.completed · T2 succeeded · commit 3c91d02a" },
    { at: "15:47:31", kind: "runner", text: "runner.completed · T4 变更采集 2 文件（仅允许路径）" },
    { at: "15:40:12", kind: "gate", text: "Security Reviewer：saleor-core 扫描 0 高危，Review 通过" },
    { at: "15:31:26", kind: "runner", text: "runner.completed · T1 succeeded · 17 测试通过 · commit 8825f6bb" },
    { at: "15:02:08", kind: "plan", text: "Task DAG v2 冻结：5 任务 · 3 层拓扑 · 合并顺序 core→dashboard/apps→docs" },
  ],

  evidence: {
    commits: ["saleor-core 8825f6bb / 3c91d02a", "saleor-apps 1de407aa（进行中）"],
    prs: ["saleor#19466（待合并）", "saleor-dashboard#6732（草稿）", "saleor-apps#2393（草稿）"],
    tests: "core 470 通过 · dashboard 203 通过 + 隐藏 3 失败 · apps CI 运行中",
    security: "2 仓扫描完成 · 0 高危 · SBOM 已生成",
    trace: "trace 53e2a455…（3 spans 已上报，含 permission.decision）",
    cost: "Token 1.24M · ¥8.42 · 已用时 2h09m · 人工介入 0 次",
  },

  rollback: [
    "撤销预发部署（未执行——尚未部署）",
    "按合并逆序 revert：docs → apps/dashboard → core",
    "core 迁移执行补偿脚本 0042_price_override_reason_down.sql",
    "恢复到基线 tag delivery-0042-base，验证四仓一致性",
  ],
};
