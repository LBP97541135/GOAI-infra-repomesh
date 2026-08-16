/** 回滚数据源（契约 v0.1 §4.6 读 / 写）。批次 E-1，设计定稿 ④。
 *
 *  这一层刻意很薄：范围表的每一行——仓名 / 当前态 / 对应动作 / 逆序第 k 步——
 *  全部由读模型给出。前端不按 `merge_sha` 判「已 merge」，也不按 `merge_order`
 *  数「第几步」：那条规则住在 delivery 的 recovery planner 里，读模型跑的是同一个
 *  planner 的预览（`preview_recovery`），前端再算一遍就是等着两边漂移。
 *
 *  **回放模式一律拒绝写**：回滚在真实世界里关 PR、开 revert PR、动 base 分支，
 *  夹具里没有可写的对象；就地演一遍「已回滚」刷新即消失，比不给按钮更误导。 */
import type { RollbackReceipt, RollbackRequest, RollbackScopeView } from "./contract";
import { defaultClient } from "./client";
import { resolveDataSourceMode } from "./source";

export async function fetchRollbackScope(roundId: string): Promise<RollbackScopeView> {
  if (resolveDataSourceMode() === "replay") {
    // 回放夹具没有回滚范围这一投影。空集会被渲染成「无可回滚项」——那是一句
    // 关于真实世界的断言，而此处根本没问过后端，所以说不知道。
    throw new Error("回放夹具未覆盖回滚范围（§4.6 投影）。加 ?source=live 后可查看真实范围。");
  }
  return defaultClient().getRollbackScope(roundId);
}

/** 幂等键随表单生成（沿 A1/A7 模式）。
 *
 *  键含 `change_set_id`：补偿后 change_set 重建时，新一轮回滚不能被判成旧决策的重放。
 *  **理由入键**——与治理批准（§4.4）相反：那里「改意见重提被去重丢弃」是审计完整性，
 *  而这里换个理由本就是后端认得的另一次请求（同理由 → 200 replayed，异理由且计划
 *  未完成 → 409）。键跟着理由走，重试同一份表单才是重放。 */
export function rollbackIdempotencyKey(changeSetId: string, reason: string): string {
  let hash = 0;
  for (const ch of reason.trim()) hash = (hash * 31 + ch.codePointAt(0)!) >>> 0;
  return `console-rollback-${changeSetId}-${hash.toString(36)}`;
}

export async function submitRollback(
  roundId: string,
  scope: RollbackScopeView,
  reason: string,
  requestedByAgentId: string,
): Promise<RollbackReceipt> {
  if (scope.change_set_id === null) {
    throw new Error("本轮没有 ChangeSet，无可回滚的交付候选。");
  }
  const payload: RollbackRequest = {
    change_set_id: scope.change_set_id,
    reason,
    requested_by_agent_id: requestedByAgentId,
    idempotency_key: rollbackIdempotencyKey(scope.change_set_id, reason),
  };
  return defaultClient().postRollback(roundId, payload);
}
