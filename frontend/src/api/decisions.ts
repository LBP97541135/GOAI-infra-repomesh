/** 决策夹数据源与写回路（v0.1 §4.3 读 / §4.4 写）。
 *
 *  决策是**轮次粒度**（`/deliveries/{round_id}/...`），而 issue 详情页是 issue 粒度：
 *  §0 语义等式 round_id = execution_plan_id = v0.1 的 delivery_id，所以详情页取
 *  `active_round_id ?? latest_round_id` 这一轮的决策，并在 UI 上标明是第几轮——
 *  `pending_decision_count` 是跨轮求和，与单轮决策数不一定相等，不能混为一谈。 */
import type { ApprovalInfo, Decision } from "../types";
import type { GovernanceDecisionRequest, GovernanceDecisionView } from "./contract";
import { createApiClient } from "./client";
import { fetchConsoleAgents } from "./grid";
import { resolveDataSourceMode } from "./source";
import { approvalFromContract, decisionsFromContract } from "../viewmodel";
import { aggregate as replayAggregate, decisionsResponse as replayDecisions } from "../data/replay";

export interface DecisionDeckData {
  deck: Decision[];
  approval: ApprovalInfo | null;
}

function client() {
  return createApiClient({
    baseUrl: import.meta.env.VITE_API_BASE ?? "",
    token: import.meta.env.VITE_API_TOKEN ?? "",
  });
}

export async function fetchDecisionDeck(roundId: string): Promise<DecisionDeckData> {
  if (resolveDataSourceMode() === "replay") {
    // 回放模式复用 v1 的演示交付（DLV-0042）——它与 v2 详情夹具不是同一 issue，
    // 所以调用方必须把这块标注为「回放演示」，不能让人以为是本 issue 的真决策。
    return {
      deck: decisionsFromContract(replayDecisions.items),
      approval: approvalFromContract(replayAggregate, replayDecisions.items),
    };
  }
  const api = client();
  const [agg, decisions] = await Promise.all([api.getDelivery(roundId), api.getDecisions(roundId)]);
  return {
    deck: decisionsFromContract(decisions.items),
    approval: approvalFromContract(agg, decisions.items),
  };
}

/** §4.4 写回路。head-bound：SHA 漂移即 409，错误原样上抛给弹窗显示，不静默。
 *  幂等键按内容确定性生成，重试自然复用同一键（后端内容重放去重）。 */
export async function submitGovernanceDecision(
  roundId: string,
  info: ApprovalInfo,
  reason: string,
  decidedByAgentId: string,
): Promise<GovernanceDecisionView> {
  if (!info.changeSetId || !info.repositoryId || !info.headSha) {
    throw new Error("授权单缺少 change_set / repository / head_sha，无法提交");
  }
  const payload: GovernanceDecisionRequest = {
    change_set_id: info.changeSetId,
    repository_id: info.repositoryId,
    head_sha: info.headSha,
    decision: "ready",
    reason,
    decided_by_agent_id: decidedByAgentId,
    idempotency_key: `console-${info.repositoryId}-${info.headSha.slice(0, 12)}-ready`,
  };
  return client().postGovernanceDecision(roundId, payload);
}

/** 治理决策主体。**从花名册派生，而不是写死一个 uuid**（主脑 2026-08-11 裁决，乙案）。
 *
 *  起因：`.env.development` 里写死的 `d2abc576-…` 已经不在种子花名册里——v2 换基线时
 *  5533 DROP 重建重种，org leader 是 v4 随机 uuid，每次重种都换一个。后端
 *  `_authorized_actor` 查不到该 principal 就拒绝，于是「真批」会当场失败。
 *
 *  但更根本的理由不是修那一行常量：决策主体的语义本来就是「**当前组织的 leader**」，
 *  而 §4.3 的花名册正好是这一事实的持久化来源。派生出来的东西重种免疫只是附带收益。
 *
 *  取值链：`VITE_GOVERNANCE_AGENT_ID`（可选覆盖，留给联调指定特定主体）→ 花名册里
 *  该组织的 active `organization_leader`。都取不到返回 `null`——调用方必须呈现
 *  「决策主体未接入」并**禁用提交**，而不是发一个注定被 403 拒绝的请求。 */
export interface GovernanceAgent {
  agentId: string;
  /** AgentTeams 资源名（不是人名），用于授权单上如实标注「谁在批」 */
  label: string;
  source: "env" | "directory";
}

/** 同一组织的解析结果缓存：授权弹窗每次打开都重解析没有意义，花名册是慢查询
 *  （带运行时探测时 ~2s，这里用 with_runtime=false 的那一版，~0.06s）。 */
const governanceAgentCache = new Map<string, Promise<GovernanceAgent | null>>();

export function resolveGovernanceAgent(organizationId: string | null): Promise<GovernanceAgent | null> {
  const key = organizationId ?? "*";
  const cached = governanceAgentCache.get(key);
  if (cached) return cached;

  const pending = (async (): Promise<GovernanceAgent | null> => {
    const override = import.meta.env.VITE_GOVERNANCE_AGENT_ID ?? "";
    if (override) return { agentId: override, label: `覆盖配置 ${override.slice(0, 8)}`, source: "env" };

    // 运行时对「谁能批」没有影响，故不请求探测（首屏两段式的同一个理由）
    const agents = await fetchConsoleAgents(false);
    const leader = agents.find(
      (a) =>
        a.role === "organization_leader" &&
        a.status === "active" &&
        // 跨组织的 leader 会被后端以「belongs to another organization」拒绝；
        // issue 的 organization_id 可能为 null（三级取值链全空），那时不加这一条筛选
        (organizationId === null || a.organization_id === organizationId),
    );
    return leader
      ? { agentId: leader.agent_id, label: leader.agentteams_resource_name, source: "directory" }
      : null;
  })();

  governanceAgentCache.set(key, pending);
  // 解析失败不缓存否定结果：花名册端点临时不可用不该让本次会话永久失去决策能力
  pending.catch(() => governanceAgentCache.delete(key));
  return pending;
}
