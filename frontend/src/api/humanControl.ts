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

// ───────────────── 监管策略草稿（迁移 5-1b，读写） ─────────────────
//
// 为什么草稿是一张独立的表、一组独立的端点，而不是搭物化的车提交：物化端点的守卫是
// **全控制台共享的动作 token**，而设策略必须是管理员。搭车会让任何持共享 token 的人
// 都能改监管强度（设计文档 §6）。所以写策略走这三个认会话的端点，物化那一步只是
// 进程内读一次这张表——**共享 token 能做的事没有变多**。
//
// ⚠ 本节全部走 `sessionRequest`（cookie 会话），与本文件其余部分同理由：带
// `Authorization` 头会让后端拿动作 token 去验会话，cookie 有效也 401。

/** `PUT /projects/{id}/policy-draft` 里的一条授权（后端 `PolicyDraftGrant`）。
 *
 *  与回读的 `HumanProjectGrantView` **形状相同但不是同一个类型**：这是请求体，
 *  后端的 `control_actions` 是 `set[...]`、`repository_id` 与 `path_patterns` 有默认值。
 *  合并成一个类型会让「我要写什么」和「服务端存成了什么」共用一份定义，日后请求体
 *  多一个字段（比如失效时间）就得在回读类型上也凭空多出来。
 *
 *  **四条单条约束由后端 `HumanProjectGrant.__post_init__` 判**（写草稿时也会跑，
 *  见 `api/human_control.py` 里刻意构造真 grant 对象的那段注释）：
 *   1. `repository_supervisor` **必须**给 `repository_id`；
 *   2. 另两种身份**不许**给 `repository_id`；
 *   3. 有 `path_patterns` 就必须先有 `repository_id`；
 *   4. `control_actions` 至少一个。
 *  界面必须自己满足这四条——被后端 422 才发现，说明界面能表达契约不允许的东西。 */
export interface PolicyDraftGrantInput {
  human_principal_id: string;
  role: HumanProjectRole;
  code_access: CodeAccessLevel;
  /** 后端收 `set`，传数组即可；重复项由后端去重，但界面不该发出重复项。 */
  control_actions: HumanControlAction[];
  /** null / 省略 = 覆盖整个项目（只有 `repository_supervisor` 能给非 null）。 */
  repository_id?: string | null;
  path_patterns?: string[];
}

/** `PUT /projects/{id}/policy-draft` 的请求体（后端 `TopologyPolicyDraftPut`）。
 *
 *  **整份覆盖写，没有幂等键**——一个需求只有一份监管意图，改主意就是替换它，
 *  没有第二份草稿可以被重复创建（后端路由文档明写）。这是它与
 *  `POST /projects/topologies` 的一处刻意不同，别照那边补一个 `idempotency_key`。
 *
 *  三个字段在后端都有默认值（`auto` / 空 / 空），但**前端一律全传**：省略字段等于
 *  让后端的默认值悄悄参与决定监管强度，而这批迁移要解决的正是「没人被问过就跑成
 *  全自动」。 */
export interface TopologyPolicyDraftInput {
  execution_mode: ProjectExecutionMode;
  required_checkpoints: ProjectCheckpoint[];
  human_grants: PolicyDraftGrantInput[];
}

/** 草稿回读体（后端 `asdict(TopologyPolicyDraft)`，字段名即后端字段名）。
 *
 *  `created_by` 是**设它的那个管理员账号**，不是被授权人——两者都只有 UUID。 */
export interface TopologyPolicyDraftView {
  project_id: string;
  created_by: string;
  execution_mode: ProjectExecutionMode;
  /** ⚠ 后端是 `frozenset[ProjectCheckpoint]`，序列化成数组时给的是
   *  **frozenset 的迭代顺序**——那个顺序受进程内哈希随机化影响，同一份草稿两次
   *  回读可以不一样。与 `GET /projects/{id}/topology` 的同名字段是同一回事。
   *
   *  **所以这是一个集合，不是一个有序列表**：不得拿它的下标、首元素或原顺序做
   *  任何事（包括「第一个卡点」这种措辞）。渲染前一律过 `display.ts` 的
   *  `orderCheckpoints()` 按流程先后定序；比较两份策略是否相同要按集合比，
   *  不能 `join()` 后比字符串——那会把同一份策略判成变了。 */
  required_checkpoints: ProjectCheckpoint[];
  human_grants: HumanProjectGrantView[];
  created_at: string;
  updated_at: string;
}

/** 读一个需求的监管策略草稿。**`project_id` 就是 `issue_id`**（契约 §0）。
 *
 *  三种失败含义各不相同，调用方必须分开呈现：
 *   - **404 = 还没设过**。这不是错误，是「未设定」这条事实本身——后端刻意不返
 *     200 空对象，因为「没人决定过」和「有人决定了不设任何卡点」是两件事，
 *     而这批迁移存在的理由正是把前者从后者里分出来；
 *   - **403 = 当前账号既不是管理员、也不在这份草稿的授权名单里**。草稿存在，
 *     只是读不到——不得渲染成「尚未设定」；
 *   - **401 = 会话过期**，重试没有意义（重试一个过期会话不会有别的结果），
 *     要指向重新登录。 */
export function fetchPolicyDraft(projectId: string): Promise<TopologyPolicyDraftView> {
  return sessionRequest<TopologyPolicyDraftView>(
    `/projects/${encodeURIComponent(projectId)}/policy-draft`,
  );
}

/** 整份覆盖写一个需求的监管策略草稿。**要管理员**（后端 `is_admin`，否则 403）。
 *
 *  可预期的失败一律 **detail 原文上抛不归并**——每一句都指名了是哪条规则、哪个字段，
 *  而调用者正是那个要去改它的人：
 *   - 403 当前账号不是管理员（改输入重试没有用）；
 *   - 422 `human grant account does not exist`（授权引用的账号不存在）；
 *   - 422 域不变量原文（`automatic projects cannot require human checkpoints` /
 *     `human-controlled projects require a human grant` /
 *     `human-controlled projects require checkpoints` /
 *     `manual-controlled projects require every human checkpoint` /
 *     `duplicate human grant scope`，以及单条授权那四条）。
 *
 *  ⚠ **这些 422 一条都不该被用户看见**：它们与界面的三档映射一一对应（设计文档
 *  §4.8），界面把这些组合排除在可表达范围之外，才算做对了。看见它们说明界面漏了
 *  一条约束——那时该修界面，不是把这句话翻译得好看一点。
 *
 *  **有一条规则这里查不了**：授权限定的仓库是否还在计划里，只有物化时才有
 *  repository_teams 可比（后端 `domain.py` 那条 `human grant references an unknown
 *  project repository`）。所以草稿存下了不等于一定能物化——用户改过分档就可能失效。 */
export function putPolicyDraft(
  projectId: string,
  payload: TopologyPolicyDraftInput,
): Promise<TopologyPolicyDraftView> {
  return sessionRequest<TopologyPolicyDraftView>(
    `/projects/${encodeURIComponent(projectId)}/policy-draft`,
    { method: "PUT", body: JSON.stringify(payload) },
  );
}

/** 撤回草稿，回到「未设定」（＝首次物化时后端补一个全自动拓扑）。**要管理员**。
 *
 *  成功是 **204 无响应体**（`sessionRequest` 在 204 上返回 undefined）。
 *  **没得撤时是 404 而不是一句轻快的 204**：以为自己撤掉了一份其实早被别人撤掉的
 *  策略，与真的撤掉了是两件事，后端选择说出来（路由文档明写）。 */
export function deletePolicyDraft(projectId: string): Promise<void> {
  return sessionRequest<void>(`/projects/${encodeURIComponent(projectId)}/policy-draft`, {
    method: "DELETE",
  });
}
