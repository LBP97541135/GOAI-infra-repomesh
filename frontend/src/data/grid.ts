/** 网格 / 团队 / 花名册 replay 夹具（契约 v0.2 §4.1 / §4.2 / §4.3）。
 *
 *  类型全在契约层 `api/contract.ts`，本文件只保留数据。沿用 issueDetail 夹具的
 *  同一场景（#7f3d2a10 结账价格修改原因，三仓），使 replay 世界自洽：这里的
 *  team/agent 与详情页的房间成员是同一批资源名。
 *
 *  夹具刻意覆盖 §4.4 的**运行时三态**——`reachable: true` / `{reachable: false}` /
 *  `null`。联调环境的 controller（8090）当前无监听，live 模式下三条全是
 *  `{reachable: false}`，只有夹具能验到 true 分支与 null 分支的渲染差异。 */
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

const TEAM_API = "c2d3e4f5-0001-4a2b-9c3d-4e5f6a7b8c01";
const TEAM_WEB = "c2d3e4f5-0002-4a2b-9c3d-4e5f6a7b8c02";
const TEAM_DOCS = "c2d3e4f5-0003-4a2b-9c3d-4e5f6a7b8c03";

const AGENT_ORG = "d3e4f5a6-0000-4a2b-9c3d-4e5f6a7b8c00";
const AGENT_LEAD_API = "d3e4f5a6-0001-4a2b-9c3d-4e5f6a7b8c01";
const AGENT_LEAD_WEB = "d3e4f5a6-0002-4a2b-9c3d-4e5f6a7b8c02";
const AGENT_LEAD_DOCS = "d3e4f5a6-0003-4a2b-9c3d-4e5f6a7b8c03";
const AGENT_WORK_API = "d3e4f5a6-0011-4a2b-9c3d-4e5f6a7b8c11";
const AGENT_WORK_WEB = "d3e4f5a6-0012-4a2b-9c3d-4e5f6a7b8c12";
const AGENT_WORK_DOCS = "d3e4f5a6-0013-4a2b-9c3d-4e5f6a7b8c13";

export const consoleRepositoriesFixture: ConsoleRepositoryView[] = [
  {
    repository_id: REPO_API,
    name: "saleor-core",
    url: "https://github.example/demo/saleor-core.git",
    description: "订单与结账域服务",
    topics: ["python", "graphql"],
    languages: ["Python"],
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
    profiled_at: "2026-08-09T09:16:00Z",
    resident_team_count: 0,
    open_issue_count: 0,
    active_task_count: 0,
    last_delivery_at: null,
    teams: [],
  },
];

export const consoleTeamsFixture: ConsoleTeamView[] = [
  {
    team_id: TEAM_API,
    agentteams_team_name: "rm-team-core",
    issue_id: ISSUE_ID,
    repository_id: REPO_API,
    repository_name: "saleor-core",
    runtime_status: "ready",
    team_room_id: "!rm-team-core:matrix.local",
    leader_room_id: "!rm-leader-core:matrix.local",
    leader: { agent_id: AGENT_LEAD_API, name: "rm-leader-core", role: "repository_leader" },
    workers: [{ agent_id: AGENT_WORK_API, name: "rm-worker-core", role: "worker" }],
    // 探测通：唯一能看到 phase 与 ready/total 的分支
    runtime: { reachable: true, phase: "Running", ready_workers: 1, total_workers: 1 },
  },
  {
    team_id: TEAM_WEB,
    agentteams_team_name: "rm-team-dashboard",
    issue_id: ISSUE_ID,
    repository_id: REPO_WEB,
    repository_name: "saleor-dashboard",
    runtime_status: "ready",
    team_room_id: "!rm-team-dashboard:matrix.local",
    leader_room_id: "!rm-leader-dashboard:matrix.local",
    leader: { agent_id: AGENT_LEAD_WEB, name: "rm-leader-dashboard", role: "repository_leader" },
    workers: [{ agent_id: AGENT_WORK_WEB, name: "rm-worker-dashboard", role: "worker" }],
    // 探测不可达：建团结果仍是 ready——两个事实并存，正是不可合并徽标的样本
    runtime: { reachable: false },
  },
  {
    team_id: TEAM_DOCS,
    agentteams_team_name: "rm-team-docs",
    issue_id: ISSUE_ID,
    repository_id: REPO_DOCS,
    repository_name: "saleor-docs",
    runtime_status: "pending",
    team_room_id: "!rm-team-docs:matrix.local",
    leader_room_id: "!rm-leader-docs:matrix.local",
    leader: { agent_id: AGENT_LEAD_DOCS, name: "rm-leader-docs", role: "repository_leader" },
    workers: [{ agent_id: AGENT_WORK_DOCS, name: "rm-worker-docs", role: "worker" }],
    // Controller 没有这个资源（404）或未配置代理：无事实可报
    runtime: null,
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
      reachable: true,
      phase: "Running",
      runtime_kind: "claude-code",
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
    agentteams_resource_name: "rm-leader-core",
    leader_agent_id: AGENT_ORG,
    repository_id: REPO_API,
    repository_name: "saleor-core",
    responsibility_paths: ["saleor/checkout/**"],
    team_id: TEAM_API,
    issue_id: ISSUE_ID,
    active_task_count: 0,
    runtime: {
      reachable: true,
      phase: "Running",
      runtime_kind: "claude-code",
      matrix_user_id: "@rm-leader-core:matrix.local",
      room_id: "!rm-leader-core:matrix.local",
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
    agentteams_resource_name: "rm-worker-core",
    leader_agent_id: AGENT_LEAD_API,
    repository_id: REPO_API,
    repository_name: "saleor-core",
    responsibility_paths: ["saleor/checkout/**", "saleor/order/**"],
    team_id: TEAM_API,
    issue_id: ISSUE_ID,
    active_task_count: 1,
    runtime: {
      reachable: true,
      phase: "Executing",
      runtime_kind: "codex",
      matrix_user_id: "@rm-worker-core:matrix.local",
      room_id: "!rm-team-core:matrix.local",
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
    agentteams_resource_name: "rm-leader-dashboard",
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
    agentteams_resource_name: "rm-worker-dashboard",
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
    agentteams_resource_name: "rm-leader-docs",
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
    agentteams_resource_name: "rm-worker-docs",
    leader_agent_id: AGENT_LEAD_DOCS,
    repository_id: REPO_DOCS,
    repository_name: "saleor-docs",
    responsibility_paths: ["docs/**"],
    team_id: TEAM_DOCS,
    issue_id: ISSUE_ID,
    active_task_count: 0,
    runtime: null,
  },
];
