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
