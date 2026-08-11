/** 决策夹数据源与写回路（v0.1 §4.3 读 / §4.4 写）。
 *
 *  决策是**轮次粒度**（`/deliveries/{round_id}/...`），而 issue 详情页是 issue 粒度：
 *  §0 语义等式 round_id = execution_plan_id = v0.1 的 delivery_id，所以详情页取
 *  `active_round_id ?? latest_round_id` 这一轮的决策，并在 UI 上标明是第几轮——
 *  `pending_decision_count` 是跨轮求和，与单轮决策数不一定相等，不能混为一谈。 */
import type { ApprovalInfo, Decision } from "../types";
import type { GovernanceDecisionRequest, GovernanceDecisionView } from "./contract";
import { createApiClient } from "./client";
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

export function governanceAgentId(): string {
  return import.meta.env.VITE_GOVERNANCE_AGENT_ID ?? "";
}
