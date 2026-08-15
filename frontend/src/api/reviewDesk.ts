/** 人工审核台（`/review-requests` 与项目检查点决策，human_control 面）。
 *
 *  **落在这里而不是 `client.ts`**：这几个端点认本地账号会话，与读模型的动作 token
 *  是两套凭据（见 `api/auth.ts` 顶部为什么不能混用）。决策要记在**谁**名下，
 *  这正是共享 token 换不来的东西——后端把 `actor.id` 直接写进决策行，前端连
 *  `human_principal_id` 这个字段都不用传。
 *
 *  **可见范围随身份变**：管理员看全部，非管理员只看指派给自己的
 *  （后端 `_reviews_for`）。所以同一个待办队列在不同账号下长度不同，这是设计而非
 *  取数不稳。 */
import { sessionRequest } from "./auth";

/** 六个检查点（后端 `ProjectCheckpoint`）。 */
export type ProjectCheckpoint =
  | "repository_scope"
  | "specification"
  | "execution"
  | "validation"
  | "delivery"
  | "exception_escalation";

/** 审核请求四态（后端 `HumanReviewStatus`）。 */
export type HumanReviewStatus = "pending" | "approved" | "rejected" | "changes_requested";

/** 决策三种（后端 `CheckpointDecisionKind`）。**比状态少一个**：没有 `pending`，
 *  因为决策一旦记下就不是待办。 */
export type CheckpointDecisionKind = "approved" | "rejected" | "changes_requested";

/** 项目控制动作（后端 `HumanControlAction`）。审核台只用得到暂停/恢复/取消三种；
 *  其余四个（`view_decisions` / `approve_checkpoint` / `request_changes` /
 *  `edit_specification`）是授权语义里的**权限名**，不是可发起的动作，故不在此
 *  列举为按钮。完整七项的类型见 `api/humanControl.ts` 的 `HumanControlAction`。 */
export type ProjectControlAction = "pause_project" | "resume_project" | "cancel_project";

export interface HumanReviewRequestView {
  id: string;
  project_id: string;
  checkpoint: ProjectCheckpoint;
  /** 决策必须钉在它实际看到的那份证据上；后端据此判定漂移 */
  evidence_version: string;
  title: string;
  summary: string;
  status: HumanReviewStatus;
  repository_id: string | null;
  requested_by_agent_id: string | null;
  resolved_by_human_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CheckpointDecisionView {
  id: string;
  review_request_id: string;
  project_id: string;
  checkpoint: ProjectCheckpoint;
  human_principal_id: string;
  decision: CheckpointDecisionKind;
  reason: string;
  repository_id: string | null;
  evidence_version: string;
  decided_at: string;
}

/** 待办列表。`status` 省略 = 全部状态（含已决的），传 `pending` = 只看待办。 */
export function fetchReviewRequests(
  status?: HumanReviewStatus,
): Promise<HumanReviewRequestView[]> {
  const q = status ? `?status=${encodeURIComponent(status)}` : "";
  return sessionRequest<HumanReviewRequestView[]>(`/review-requests${q}`);
}

/** 记一条检查点决策。
 *
 *  **决策人不由前端指定**：后端取会话账号写入 `human_principal_id`。这是恢复登录门
 *  换来的东西——共享动作 token 下这一列只能是「某个人」。
 *
 *  409 = 该检查点已有决策或证据已漂移；403 = 该账号在这个项目里没有决策权。
 *  两者 detail 原文上抛，各有各的下一步。 */
export function recordCheckpointDecision(
  projectId: string,
  payload: { review_request_id: string; decision: CheckpointDecisionKind; reason: string },
): Promise<CheckpointDecisionView> {
  return sessionRequest<CheckpointDecisionView>(
    `/projects/${encodeURIComponent(projectId)}/checkpoint-decisions`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

/** 暂停 / 恢复 / 取消一个项目。403 = 该账号没有这项控制权。 */
export function controlProject(
  projectId: string,
  action: ProjectControlAction,
): Promise<unknown> {
  return sessionRequest<unknown>(`/projects/${encodeURIComponent(projectId)}/control`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });
}

/** 待办的 SSE 流（`event: review-requests`，每 2s 比对、变了才推）。
 *
 *  **不带 Authorization 头**——EventSource 本来也不支持自定义头，而这一面认 cookie，
 *  正好合得上。`withCredentials` 只有跨源才需要；控制台走同源 dev proxy，
 *  同源请求默认就带 cookie。
 *
 *  返回取消函数。调用方负责在卸载时调用它，否则连接会一直挂着。 */
export function subscribeReviewRequests(
  onData: (rows: HumanReviewRequestView[]) => void,
  onError: () => void,
): () => void {
  const base = import.meta.env.VITE_API_BASE ?? "";
  const source = new EventSource(`${base}/api/v1/review-requests/events`, {
    withCredentials: true,
  });
  source.addEventListener("review-requests", (event) => {
    try {
      onData(JSON.parse((event as MessageEvent<string>).data) as HumanReviewRequestView[]);
    } catch {
      // 解析不了就当这一帧没来过：SSE 会继续推下一帧，
      // 为一帧坏数据把整条流判死不值得。
    }
  });
  source.onerror = onError;
  return () => source.close();
}
