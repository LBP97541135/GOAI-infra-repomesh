import type { IssueListItemView, RuntimeBlock } from "./api/contract";

/** issue 与网格页的展示辅助。**纯格式化**，不含任何状态派生——state/phase/
 *  phase_note/runtime.phase 一律由读模型给出（契约红线）。多页共用同一份，避免漂移。
 *  （放这里而不是 viewmodel.ts：后者依赖 v1 的 DeliveryData，随清理批次 #17 退役。） */

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

/** 仓库展示名。`repository_name` 在 §4.2 / §5.1 / §4.3 三处**同一派生**，
 *  catalog 查不到该 repository_id 时都是 `null`（拓扑驻扎的仓库未必在 catalog 里）——
 *  契约 §7.3 勘误把三处统一标成 nullable 后，降级措辞也收敛到这一处：
 *  三个页面各写一套「查不到怎么显示」，迟早会有一处漏判直接渲染出 `null`。 */
export function repositoryLabel(name: string | null, repositoryId: string): string {
  return name ?? `仓库 ${shortId(repositoryId)}（catalog 未收录）`;
}

/** 两段式取数的探测阶段（团队页 / 花名册页共用，见 pages/useRuntimeRows.ts）。 */
export type RuntimePhase = "loading" | "done" | "failed";

/** §4.4 运行时块的**五种呈现**。前四种都不是「有观测值」，措辞必须各不相同——
 *  把它们糊成同一句「未接入」会丢掉「探测过但打不通」与「压根没有这个资源」的区别。
 *
 *  尤其是 `unreachable`：契约明写这是**降级不是故障**（HTTP 仍 200，持久化事实照常可读），
 *  所以文案只说「运行时探测不可达」，绝不说团队/智能体坏了。 */
export type RuntimeDisplay =
  | { kind: "probing"; label: string; hint: string }
  | { kind: "probe_failed"; label: string; hint: string }
  | { kind: "unreachable"; label: string; hint: string }
  | { kind: "absent"; label: string; hint: string }
  | { kind: "observed"; label: string; hint: string };

export function runtimeDisplay(phase: RuntimePhase, runtime: RuntimeBlock<{ phase: string | null }>): RuntimeDisplay {
  // 首段的 runtime 恒为 null（没请求探测），此时 null 不表示「没有」——不得据此判定
  if (phase === "loading") {
    return { kind: "probing", label: "探测中…", hint: "运行时状态由 AgentTeams Controller 实时代理，正在探测" };
  }
  if (phase === "failed" && runtime === null) {
    return { kind: "probe_failed", label: "探测请求失败", hint: "运行时探测请求本身失败；下方持久化事实不受影响" };
  }
  // label 不自带「运行时」三字：调用处（团队页徽标前缀 / 花名册的运行时列头）
  // 已经提供了这个语境，重复会读成「运行时 · 运行时探测不可达」
  if (runtime === null) {
    return { kind: "absent", label: "未接入", hint: "AgentTeams 未配置，或 Controller 报告没有这个资源（404）" };
  }
  if (!runtime.reachable) {
    return { kind: "unreachable", label: "探测不可达", hint: "Controller 未响应（超时或网络错误）。这是降级不是故障——持久化事实仍然为真" };
  }
  // phase 是 Controller 的字面值，前端只透传不映射
  return {
    kind: "observed",
    label: runtime.phase ?? "阶段未回报",
    hint: runtime.phase ? "Controller 当前观测阶段" : "Controller 可达但未回报 phase",
  };
}
