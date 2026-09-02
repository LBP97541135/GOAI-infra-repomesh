/** 网格 / 团队 / 花名册 replay 夹具（契约 v0.2 §4.1 / §4.2 / §4.3）。
 *
 *  类型全在契约层 `api/contract.ts`，本文件只保留数据。沿用 issueDetail 夹具的
 *  同一场景（#7f3d2a10 结账价格修改原因，三仓），使 replay 世界自洽：这里的
 *  team/agent 与详情页的房间成员是同一批资源名。
 *
 *  夹具刻意覆盖 §4.4 的**运行时三态**——`reachable: true` / `{reachable: false}` /
 *  `null`。联调环境的 controller（8090）当前无监听，live 模式下三条全是
 *  `{reachable: false}`，只有夹具能验到 true 分支与 null 分支的渲染差异。
 *
 *  探通的那几条再分 `kind` 三值（container / external / null=没问）：external
 *  行是本地 CLI 经 Bridge 接入的成员，`phase` 与 `runtime_kind` 恒 null——replay
 *  下打开花名册就该看到它写 External 而不是任何容器阶段词。 */
import type {
  ConsoleAgentView,
  ConsoleRepositoryView,
  ConsoleTeamView,
} from "../api/contract";

const ISSUE_ID = "7f3d2a10-93d0-4c8e-9b21-5aa1c0de0042";
const ORG_ID = "0a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d";
const REPO_API = "b1c2d3e4-0001-4a2b-9c3d-4e5f6a7b8c01";
const REPO_WEB = "b1c2d3e4-0002-4a2b-9c3d-4e5f6a7b8c02";
const REPO_DOCS = "b1c2d3e4-0003-4a2b-9c3d-4e5f6a7b8c03";
/** 第四个仓库：catalog 里有、但没有任何团队驻扎——「无驻扎团队」不是错误态 */
const REPO_IDLE = "b1c2d3e4-0004-4a2b-9c3d-4e5f6a7b8c04";
/** 第五个仓库：测试资产仓，贴 `cross-repo-test-team` 档——TeamsPage 徽标 join
 *  的正样本（其余仓 null 即反样本），也是 RepositoriesPage 档案回显的样本 */
const REPO_TEST = "b1c2d3e4-0005-4a2b-9c3d-4e5f6a7b8c05";

const TEAM_API = "c2d3e4f5-0001-4a2b-9c3d-4e5f6a7b8c01";
const TEAM_WEB = "c2d3e4f5-0002-4a2b-9c3d-4e5f6a7b8c02";
const TEAM_DOCS = "c2d3e4f5-0003-4a2b-9c3d-4e5f6a7b8c03";
const TEAM_TEST = "c2d3e4f5-0004-4a2b-9c3d-4e5f6a7b8c04";

const AGENT_ORG = "d3e4f5a6-0000-4a2b-9c3d-4e5f6a7b8c00";
const AGENT_LEAD_API = "d3e4f5a6-0001-4a2b-9c3d-4e5f6a7b8c01";
const AGENT_LEAD_WEB = "d3e4f5a6-0002-4a2b-9c3d-4e5f6a7b8c02";
const AGENT_LEAD_DOCS = "d3e4f5a6-0003-4a2b-9c3d-4e5f6a7b8c03";
const AGENT_LEAD_TEST = "d3e4f5a6-0004-4a2b-9c3d-4e5f6a7b8c04";
const AGENT_WORK_API = "d3e4f5a6-0011-4a2b-9c3d-4e5f6a7b8c11";
const AGENT_WORK_WEB = "d3e4f5a6-0012-4a2b-9c3d-4e5f6a7b8c12";
const AGENT_WORK_DOCS = "d3e4f5a6-0013-4a2b-9c3d-4e5f6a7b8c13";
const AGENT_WORK_TEST = "d3e4f5a6-0014-4a2b-9c3d-4e5f6a7b8c14";

export const consoleRepositoriesFixture: ConsoleRepositoryView[] = [
  {
    repository_id: REPO_API,
    name: "saleor-core",
    url: "https://github.example/demo/saleor-core.git",
    description: "订单与结账域服务",
    topics: ["python", "graphql"],
    languages: ["Python"],
    test_commands: ["python scripts/run_tests.py"],
    test_paths: ["tests/**"],
    capability_profile: null,
    profiled_at: "2026-08-09T09:12:00Z",
    resident_team_count: 1,
    open_issue_count: 1,
    active_task_count: 1,
    last_delivery_at: "2026-08-11T16:58:00Z",
    teams: [{ team_id: TEAM_API, issue_id: ISSUE_ID, runtime_status: "ready" }],
  },
  {
    repository_id: REPO_WEB,
    name: "saleor-dashboard",
    url: "https://github.example/demo/saleor-dashboard.git",
    description: "后台管理前端",
    topics: ["typescript", "react"],
    languages: ["TypeScript"],
    test_commands: ["npm test"],
    test_paths: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    capability_profile: null,
    profiled_at: "2026-08-09T09:14:00Z",
    resident_team_count: 1,
    open_issue_count: 1,
    active_task_count: 0,
    last_delivery_at: "2026-08-11T17:02:00Z",
    teams: [{ team_id: TEAM_WEB, issue_id: ISSUE_ID, runtime_status: "ready" }],
  },
  {
    repository_id: REPO_DOCS,
    name: "saleor-docs",
    url: "https://github.example/demo/saleor-docs.git",
    description: "",
    topics: [],
    languages: ["MDX"],
    test_commands: [],
    test_paths: [],
    capability_profile: null,
    profiled_at: "2026-08-09T09:15:00Z",
    resident_team_count: 1,
    open_issue_count: 1,
    active_task_count: 0,
    last_delivery_at: null,
    teams: [{ team_id: TEAM_DOCS, issue_id: ISSUE_ID, runtime_status: "pending" }],
  },
  {
    repository_id: REPO_IDLE,
    name: "saleor-storefront",
    url: "https://github.example/demo/saleor-storefront.git",
    description: "面向买家的店面",
    topics: [],
    languages: ["TypeScript"],
    test_commands: [],
    test_paths: [],
    capability_profile: null,
    profiled_at: "2026-08-09T09:16:00Z",
    resident_team_count: 0,
    open_issue_count: 0,
    active_task_count: 0,
    last_delivery_at: null,
    teams: [],
  },
  {
    repository_id: REPO_TEST,
    name: "repomesh-test-assets",
    url: "https://github.example/demo/repomesh-test-assets.git",
    description: "测试资产仓：场景库、环境定义与联调证据的唯一归宿",
    topics: ["integration-test"],
    languages: [],
    test_commands: [],
    test_paths: [],
    // 档案开关拨在测试团队档上：TeamsPage 徽标 join 的正样本
    capability_profile: "cross-repo-test-team",
    profiled_at: "2026-08-09T09:18:00Z",
    resident_team_count: 1,
    open_issue_count: 0,
    active_task_count: 0,
    last_delivery_at: null,
    teams: [{ team_id: TEAM_TEST, issue_id: ISSUE_ID, runtime_status: "ready" }],
  },
];

export const consoleTeamsFixture: ConsoleTeamView[] = [
  {
    team_id: TEAM_API,
    agentteams_team_name: "repomesh-team-core",
    issue_id: ISSUE_ID,
    repository_id: REPO_API,
    repository_name: "saleor-core",
    runtime_status: "ready",
    // 采用了该仓库已绑定的外部 Repository Leader：批次会停在 leader 任务（D-2）
    decomposition_mode: "leader",
    team_room_id: "!repomesh-team-core:matrix.local",
    leader_room_id: "!repomesh-leader-core:matrix.local",
    leader: { agent_id: AGENT_LEAD_API, name: "repomesh-leader-core", role: "repository_leader" },
    workers: [{ agent_id: AGENT_WORK_API, name: "repomesh-worker-core", role: "worker" }],
    // 探测通：唯一能看到 phase 与 ready/total 的分支
    runtime: { reachable: true, phase: "Running", ready_workers: 1, total_workers: 1 },
  },
  {
    team_id: TEAM_WEB,
    agentteams_team_name: "repomesh-team-dashboard",
    issue_id: ISSUE_ID,
    repository_id: REPO_WEB,
    repository_name: "saleor-dashboard",
    runtime_status: "ready",
    decomposition_mode: "server",
    team_room_id: "!repomesh-team-dashboard:matrix.local",
    leader_room_id: "!repomesh-leader-dashboard:matrix.local",
    leader: { agent_id: AGENT_LEAD_WEB, name: "repomesh-leader-dashboard", role: "repository_leader" },
    workers: [{ agent_id: AGENT_WORK_WEB, name: "repomesh-worker-dashboard", role: "worker" }],
    // 探测不可达：建团结果仍是 ready——两个事实并存，正是不可合并徽标的样本
    runtime: { reachable: false },
  },
  {
    team_id: TEAM_DOCS,
    agentteams_team_name: "repomesh-team-docs",
    issue_id: ISSUE_ID,
    repository_id: REPO_DOCS,
    repository_name: "saleor-docs",
    runtime_status: "pending",
    decomposition_mode: "server",
    team_room_id: "!repomesh-team-docs:matrix.local",
    leader_room_id: "!repomesh-leader-docs:matrix.local",
    leader: { agent_id: AGENT_LEAD_DOCS, name: "repomesh-leader-docs", role: "repository_leader" },
    workers: [{ agent_id: AGENT_WORK_DOCS, name: "repomesh-worker-docs", role: "worker" }],
    // Controller 没有这个资源（404）或未配置代理：无事实可报
    runtime: null,
  },
  {
    team_id: TEAM_TEST,
    agentteams_team_name: "repomesh-team-test-assets",
    issue_id: ISSUE_ID,
    repository_id: REPO_TEST,
    repository_name: "repomesh-test-assets",
    runtime_status: "ready",
    decomposition_mode: "server",
    team_room_id: "!repomesh-team-test-assets:matrix.local",
    leader_room_id: "!repomesh-leader-test-assets:matrix.local",
    leader: {
      agent_id: AGENT_LEAD_TEST,
      name: "repomesh-leader-test-assets",
      role: "repository_leader",
    },
    workers: [
      { agent_id: AGENT_WORK_TEST, name: "repomesh-worker-test-assets", role: "worker" },
    ],
    // 锚定在贴档仓上的团队：徽标来自仓库档案的 join，不来自本视图的任何字段
    runtime: { reachable: true, phase: "Running", ready_workers: 1, total_workers: 1 },
  },
];

export const consoleAgentsFixture: ConsoleAgentView[] = [
  {
    agent_id: AGENT_ORG,
    organization_id: ORG_ID,
    role: "organization_leader",
    status: "active",
    agentteams_resource_name: "demo-org-leader",
    leader_agent_id: null,
    repository_id: null,
    repository_name: null,
    responsibility_paths: [],
    team_id: null,
    issue_id: null,
    active_task_count: 0,
    runtime: {
      // manager 探测不带 containerManaged：托管方式是**未知**，不是 external
      kind: null,
      reachable: true,
      phase: "Running",
      runtime_kind: "openclaw",
      matrix_user_id: "@demo-org-leader:matrix.local",
      room_id: null,
      message: null,
      awake: null,
      uptime_seconds: null,
    },
  },
  {
    agent_id: AGENT_LEAD_API,
    organization_id: ORG_ID,
    role: "repository_leader",
    status: "active",
    agentteams_resource_name: "repomesh-leader-core",
    leader_agent_id: AGENT_ORG,
    repository_id: REPO_API,
    repository_name: "saleor-core",
    responsibility_paths: ["saleor/checkout/**"],
    team_id: TEAM_API,
    issue_id: ISSUE_ID,
    active_task_count: 0,
    runtime: {
      // 本仓库的 leader 正是被采用的**外部** Repository Leader（团队夹具的
      // decomposition_mode: "leader" 说的就是他）。Controller 核实容器不归它管，
      // 所以 phase 与 runtime_kind 没有主语——服务端投影已扣掉 Controller 那句
      // 默认的 "Pending"，夹具必须同形，否则 replay 世界会把这条渲染成容器成员。
      kind: "external",
      reachable: true,
      phase: null,
      runtime_kind: null,
      matrix_user_id: "@repomesh-leader-core:matrix.local",
      room_id: "!repomesh-leader-core:matrix.local",
      message: null,
      awake: null,
      uptime_seconds: null,
    },
  },
  {
    agent_id: AGENT_WORK_API,
    organization_id: ORG_ID,
    role: "worker",
    status: "active",
    agentteams_resource_name: "repomesh-worker-core",
    leader_agent_id: AGENT_LEAD_API,
    repository_id: REPO_API,
    repository_name: "saleor-core",
    responsibility_paths: ["saleor/checkout/**", "saleor/order/**"],
    team_id: TEAM_API,
    issue_id: ISSUE_ID,
    active_task_count: 1,
    runtime: {
      kind: "container",
      reachable: true,
      phase: "Executing",
      runtime_kind: "hermes",
      matrix_user_id: "@repomesh-worker-core:matrix.local",
      room_id: "!repomesh-team-core:matrix.local",
      message: "任务执行中",
      awake: null,
      uptime_seconds: null,
    },
  },
  {
    agent_id: AGENT_LEAD_WEB,
    organization_id: ORG_ID,
    role: "repository_leader",
    status: "active",
    agentteams_resource_name: "repomesh-leader-dashboard",
    leader_agent_id: AGENT_ORG,
    repository_id: REPO_WEB,
    repository_name: "saleor-dashboard",
    responsibility_paths: ["src/**"],
    team_id: TEAM_WEB,
    issue_id: ISSUE_ID,
    active_task_count: 0,
    runtime: { reachable: false },
  },
  {
    agent_id: AGENT_WORK_WEB,
    organization_id: ORG_ID,
    role: "worker",
    status: "active",
    agentteams_resource_name: "repomesh-worker-dashboard",
    leader_agent_id: AGENT_LEAD_WEB,
    repository_id: REPO_WEB,
    repository_name: "saleor-dashboard",
    responsibility_paths: ["src/orders/**"],
    team_id: TEAM_WEB,
    issue_id: ISSUE_ID,
    active_task_count: 0,
    runtime: { reachable: false },
  },
  {
    agent_id: AGENT_LEAD_DOCS,
    organization_id: ORG_ID,
    role: "repository_leader",
    status: "active",
    agentteams_resource_name: "repomesh-leader-docs",
    leader_agent_id: AGENT_ORG,
    repository_id: REPO_DOCS,
    repository_name: "saleor-docs",
    responsibility_paths: ["docs/**"],
    team_id: TEAM_DOCS,
    issue_id: ISSUE_ID,
    active_task_count: 0,
    runtime: null,
  },
  {
    agent_id: AGENT_WORK_DOCS,
    organization_id: ORG_ID,
    role: "worker",
    status: "disabled",
    agentteams_resource_name: "repomesh-worker-docs",
    leader_agent_id: AGENT_LEAD_DOCS,
    repository_id: REPO_DOCS,
    repository_name: "saleor-docs",
    responsibility_paths: ["docs/**"],
    team_id: TEAM_DOCS,
    issue_id: ISSUE_ID,
    active_task_count: 0,
    runtime: null,
  },
  {
    agent_id: AGENT_LEAD_TEST,
    organization_id: ORG_ID,
    role: "repository_leader",
    status: "active",
    agentteams_resource_name: "repomesh-leader-test-assets",
    leader_agent_id: AGENT_ORG,
    repository_id: REPO_TEST,
    repository_name: "repomesh-test-assets",
    responsibility_paths: ["scenarios/**", "environments/**"],
    team_id: TEAM_TEST,
    issue_id: ISSUE_ID,
    active_task_count: 0,
    runtime: { reachable: false },
  },
  {
    agent_id: AGENT_WORK_TEST,
    organization_id: ORG_ID,
    role: "worker",
    status: "active",
    agentteams_resource_name: "repomesh-worker-test-assets",
    leader_agent_id: AGENT_LEAD_TEST,
    repository_id: REPO_TEST,
    repository_name: "repomesh-test-assets",
    responsibility_paths: ["evidence/**"],
    team_id: TEAM_TEST,
    issue_id: ISSUE_ID,
    active_task_count: 0,
    runtime: { reachable: false },
  },
];
