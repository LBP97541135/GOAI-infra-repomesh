/** human_control 面客户端（`/api/v1` 上由本地账号会话把守的那一半）。
 *
 *  **与 `api/client.ts` 是两套凭据，不可互换**：那边走 vite env 注入的共享动作
 *  token，这边走登录门发的 httpOnly cookie 会话。分成两个模块而不是给 client.ts
 *  加一个 `auth` 参数，是因为混用不是风格问题而是会静默失败——后端 `_bearer()`
 *  先读 `Authorization` 头、取不到才回落 cookie，所以带着动作 token 打这些端点，
 *  会拿动作 token 去验会话直接 401，cookie 明明有效也进不来。两个模块各自持有
 *  自己的通道，就没有哪个调用点需要记住这条规则。
 *
 *  通道实现只有一处（`auth.ts` 的 `sessionRequest`），本模块只声明端点。 */
import { sessionRequest } from "./auth";
import type { TeamRuntimeStatus } from "./contract";
import type { ProjectCheckpoint } from "./reviewDesk";

/** `agent_directory` 的 `AgentPrincipalView`（后端 `asdict` 直出）。 */
export interface AgentPrincipalView {
  id: string;
  organization_id: string;
  role: "organization_leader" | "repository_leader" | "worker";
  leader_agent_id: string | null;
  repository_id: string | null;
  responsibility_paths: string[];
  agentteams_resource_name: string;
  status: string;
}

/** `TeamRuntimeRef`——建团后 Controller 回报的运行时事实。
 *  `phase` 是 Controller 的原话（`ready` / `active` / `running` / …），不做归一：
 *  归一表在后端 `project_topology.py`，前端再写一份必然与它漂移。 */
export interface TeamRuntimeRef {
  name: string;
  phase: string;
  team_room_id: string | null;
  leader_room_id: string | null;
  leader_name: string;
  ready_workers: number;
  total_workers: number;
}

export interface RepositoryTeamOnboardResult {
  repository_id: string;
  repository_name: string;
  leader: AgentPrincipalView;
  workers: AgentPrincipalView[];
  team: TeamRuntimeRef;
}

export interface RepositoryTeamOnboardRequest {
  organization_id: string;
  /** 后端 ge=1 le=20；默认 1 */
  worker_count?: number;
  /** 省略则用服务端 `settings.deepseek_model`——前端不镜像那个默认值 */
  model?: string;
  responsibility_paths?: string[];
  idempotency_key: string;
}

/** 给一个已注册仓库建出常驻的 Leader / Workers 及其 AgentTeams Team。
 *
 *  这是控制台**唯一**的建团入口，补的是扫描接入只写 catalog、不建团留下的那段：
 *  仓库接入完停在「团队待建」，此前控制台没有地方能把它推到就绪。
 *
 *  **要管理员**（后端 `is_admin`，非管理员 403）。其余可预期的失败都带可行动的
 *  detail，一律原文上抛而不归并：404 = 仓库未注册（先扫描）、422 = 该组织不是
 *  恰好一个活跃 Org Leader、503 = AgentTeams 控制面未配置或拒绝。
 *
 *  幂等键由调用方持有：后端用它派生 `:leader` / `:worker:NN` / `:team` 三段子键，
 *  所以同键重放不会建出第二套人马。 */
export function onboardRepositoryTeam(
  repositoryId: string,
  payload: RepositoryTeamOnboardRequest,
): Promise<RepositoryTeamOnboardResult> {
  return sessionRequest<RepositoryTeamOnboardResult>(
    `/repositories/${encodeURIComponent(repositoryId)}/agent-team`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

// ───────────────── 项目监管策略（迁移 5-1a，只读） ─────────────────

/** 后端 `ProjectExecutionMode`。三档不是程度递进，而是**域不变量各不相同**
 *  （`modules/project/domain.py` 的 `__post_init__`）：
 *   - `auto`：`required_checkpoints` **必须为空**（auto 带卡点会被域拒绝），
 *     且 `requires_human_checkpoint()` 恒返回 false——一个人工卡点都不会有；
 *   - `supervised`：至少一个卡点 + 至少一条 human_grant，两者缺一域都不收；
 *   - `manual_controlled`：`required_checkpoints` **必须是全部六个**，少一个就拒。
 *  所以三档不是「设一下更严一点」，而是三种形状。 */
export type ProjectExecutionMode = "auto" | "supervised" | "manual_controlled";

/** 后端 `HumanProjectRole` / `CodeAccessLevel` / `HumanControlAction`。
 *  照抄后端枚举，前端不另立一套（迁移文档 §3 明文）。 */
export type HumanProjectRole =
  | "organization_supervisor"
  | "project_supervisor"
  | "repository_supervisor";

export type CodeAccessLevel = "none" | "read" | "write";

export type HumanControlAction =
  | "view_decisions"
  | "approve_checkpoint"
  | "request_changes"
  | "pause_project"
  | "resume_project"
  | "cancel_project"
  | "edit_specification";

/** 一条人工授权（后端 `HumanProjectGrantView`）。
 *
 *  `human_principal_id` 指向 `identity_access.local_human_accounts`，**只有 UUID，
 *  没有人名**——把它显示成名字要另调 `GET /auth/accounts`，而那个端点只有管理员
 *  能调（取舍见 `IssueDetailPage` 的 `SupervisionBlock` 注释）。
 *
 *  `repository_id` 为 null = 该授权覆盖整个项目；`path_patterns` 为空 = 不限路径。 */
export interface HumanProjectGrantView {
  human_principal_id: string;
  role: HumanProjectRole;
  code_access: CodeAccessLevel;
  /** 后端是 `frozenset`，序列化成数组**顺序不确定**。同 `required_checkpoints`。 */
  control_actions: HumanControlAction[];
  repository_id: string | null;
  path_patterns: string[];
}

/** 拓扑上的一支仓库团队（后端 `RepositoryTeamView`）。
 *  与读模型 §3 的 `IssueTeamRef` 是同一批行的两个投影，字段更全。 */
export interface ProjectRepositoryTeamView {
  id: string;
  project_id: string;
  repository_id: string;
  leader_agent_id: string;
  worker_agent_ids: string[];
  agentteams_team_name: string;
  runtime_status: TeamRuntimeStatus;
  room_id: string | null;
  leader_room_id: string | null;
}

/** 后端 `ProjectAgentTopologyView`（路由 `asdict` 直出，字段名即后端字段名）。 */
export interface ProjectAgentTopologyView {
  id: string;
  organization_id: string;
  project_id: string;
  organization_leader_id: string;
  repository_teams: ProjectRepositoryTeamView[];
  execution_mode: ProjectExecutionMode;
  /** ⚠ 后端是 `frozenset[ProjectCheckpoint]`，序列化成数组**顺序不确定**——
   *  同一个项目每次刷新顺序都可能不同。渲染前必须过 `orderCheckpoints()`
   *  按流程先后定序，否则界面上的卡点次序会自己跳。 */
  required_checkpoints: ProjectCheckpoint[];
  human_grants: HumanProjectGrantView[];
  operational_status: "active" | "paused" | "cancelled";
}

/** 读一个 issue 的项目监管策略（执行方式 + 人工检查点 + 授权人）。
 *
 *  **`project_id` 就是 `issue_id`**（契约 §0 语义等式，见 `api/rooms.ts`）——控制台
 *  里没有独立于 issue 的项目，传别的东西只会 404。
 *
 *  **走 cookie 会话，绝不带 `Authorization` 头**（见本文件头）：后端 `_bearer()`
 *  先读该头、取不到才回落 cookie，带上动作 token 会让有效 cookie 也 401。
 *
 *  三种失败**含义完全不同，调用方必须分开呈现**（后端 `get_project_topology`）：
 *   - **404 = 该 issue 还没有拓扑，这不是错误**：监管策略尚未设定。控制台的物化路径
 *     会在干活途中自动建一个 `auto` + 零卡点的拓扑（`EnsureProjectAgentTopology`），
 *     所以这个 404 同时也是「还来得及设定」的窗口；
 *   - **403 = 当前账号既不是管理员、也不在本项目的 `human_grants` 里**：策略存在，
 *     只是这个账号读不到。**看不到不等于没有**，不得渲染成「尚未设定」；
 *   - **401 = 登录会话过期**，这一态**重试没有意义**（重试一个过期会话不会有别的
 *     结果），要指向重新登录。这一段是本页唯一认会话的取数，所以别处还读得到、
 *     只有它读不到；
 *   - 其余 = 真的取数失败，可重试。
 *  404 的判定在权限判定之前，所以「没有拓扑」对所有账号都如实答 404，不会被权限盖住。 */
export function fetchProjectTopology(projectId: string): Promise<ProjectAgentTopologyView> {
  return sessionRequest<ProjectAgentTopologyView>(
    `/projects/${encodeURIComponent(projectId)}/topology`,
  );
}
