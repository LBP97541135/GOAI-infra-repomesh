import type { IssueListItemView } from "./api/contract";

/** issue 展示辅助。**纯格式化**，不含任何状态派生——state/phase/phase_note 一律由
 *  读模型给出（契约红线）。列表页与详情页共用同一份，避免两处漂移。 */

/** uuid 短版。`issue_key` 恒 null（无 Project 注册表，§0/§6.1），所以 issue 的
 *  人类可读标识只能是它，不得自造 GitHub 式序号。 */
export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—";
}

/** ISO 时间戳 → MM-DD。取不到格式时原样回显，不猜。 */
export function dayLabel(at: string): string {
  const m = at.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[2]}-${m[3]}` : at;
}

/** 发起人恒为 **agent**（最早 PlanSnapshot 的 created_by_agent_id）。
 *  `opened_by_name` 是 AgentTeams 资源名（rm-worker-01 这类），与 §4.2 的
 *  `sender_name` 同源同精度，**不是人名**——AGENT 前缀必须保留，否则读者会
 *  以为这单是同事开的。两者都取不到时显「发起人未关联」，不编造。 */
export function openedBy(item: Pick<IssueListItemView, "opened_by_name" | "opened_by_agent_id">): string {
  if (item.opened_by_name) return `AGENT ${item.opened_by_name}`;
  if (item.opened_by_agent_id) return `AGENT ${shortId(item.opened_by_agent_id)}`;
  return "发起人未关联";
}
