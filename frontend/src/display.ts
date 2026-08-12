import type {
  DeliveryTaskView,
  DiscoveryTier,
  DiscoveryTierStatus,
  GovernanceDecisionView,
  IssueListItemView,
  Phase,
  RollbackRepositoryAction,
  RollbackRepositoryState,
  RuntimeBlock,
} from "./api/contract";

/** issue 与网格页的展示辅助。**纯格式化**，不含任何状态派生——state/phase/
 *  phase_note/runtime.phase 一律由读模型给出（契约红线）。多页共用同一份，避免漂移。 */

/** 八相皮肤唯一表（X2 合并：此前三页各持一份，release 在列表页灰、详情页琥珀——
 *  主脑裁决琥珀为正）。展示皮肤，不是状态映射；类型收窄到 Phase（缺项=编译错误），
 *  消费方保留运行时兜底（X6：服务端先于前端演进时优雅降级）。 */
export const PHASE_SKIN: Record<Phase, { dot: string; badge: string }> = {
  contract: { dot: "bg-tx2", badge: "border-line text-tx2" },
  plan: { dot: "bg-tx2", badge: "border-line text-tx2" },
  execute: { dot: "bg-bluegray", badge: "border-bluegray text-bluegray" },
  validate: { dot: "bg-salmon", badge: "border-salmon text-salmon" },
  release: { dot: "bg-amber", badge: "border-amber text-amber" },
  delivered: { dot: "bg-olive", badge: "border-olive text-olive" },
  failed: { dot: "bg-salmon", badge: "border-salmon text-salmon" },
  archived: { dot: "bg-tx2", badge: "border-line text-tx2" },
};

export const PHASE_SKIN_FALLBACK = { dot: "bg-tx2", badge: "border-line text-tx2" };

/** 建团三态措辞与皮肤唯一表（X3 合并：主脑裁决「团队待建」为正——「建团中」暗示
 *  进行中动作，而拓扑只记录「未就绪」，措辞不得超出事实精度）。 */
export const TEAM_STATUS_LABEL: Record<"pending" | "ready" | "failed", string> = {
  pending: "团队待建",
  ready: "团队就绪",
  failed: "建团失败",
};

export const TEAM_STATUS_SKIN: Record<"pending" | "ready" | "failed", string> = {
  pending: "border-line text-tx2",
  ready: "border-olive text-olive",
  failed: "border-salmon text-salmon",
};

/** 发现链三档的措辞与皮肤唯一表（契约 v0.4）。三色沿决策夹标签色（设计定稿
 *  「新界面不新增颜色语义」）：橄榄绿 = 必需、琥珀 = 可能、赭红 = 排除。
 *  展示皮肤，不是状态映射——生效分档由读模型的 `effective_tiers` 给出。 */
export const TIER_LABEL: Record<DiscoveryTier, string> = {
  required: "必需",
  maybe: "可能",
  excluded: "排除",
};

export const TIER_SKIN: Record<DiscoveryTier, string> = {
  required: "border-olive text-olive",
  maybe: "border-amber text-amber",
  excluded: "border-salmon text-salmon",
};

/** 大写 `ConfirmationResult.status` / `adjustments.from|to` → 小写档位。
 *  契约 §1.1：「大小写映射必须只有一处实现」——就是这里，别在组件里再写第二个。
 *  运行时兜底不猜新值：认不出就原样小写回显（宁可显示一个陌生词，也不静默归到某一档）。 */
const TIER_OF: Record<DiscoveryTierStatus, DiscoveryTier> = {
  REQUIRED: "required",
  MAYBE: "maybe",
  EXCLUDED: "excluded",
};

export function tierOf(status: string): DiscoveryTier | null {
  return TIER_OF[status as DiscoveryTierStatus] ?? null;
}

/** 大写档位的展示文案：认得出走三档表，认不出原样透出服务端字面值。 */
export function tierStatusLabel(status: string): string {
  const tier = tierOf(status);
  return tier ? TIER_LABEL[tier] : status;
}

/** uuid 短版。`issue_key` 恒 null（无 Project 注册表，§0/§6.1），所以 issue 的
 *  人类可读标识只能是它，不得自造 GitHub 式序号。 */
export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—";
}

/** 错误 → 展示文案。全仓 catch 分支的同一句三元收在这一处。 */
export function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** agent 展示名：资源名有值直用，否则 AGENT + id 短版（与 §4.2 sender_name
 *  的诚实降级同款——不编造名字）。 */
export function agentLabel(name: string | null, agentId: string): string {
  return name ?? `AGENT ${shortId(agentId)}`;
}

/** 治理决策徽标措辞唯一表。Record 收窄到契约枚举：新增决策值时缺项即编译错误；
 *  运行时兜底 toUpperCase 只服务尚未收窄的字符串消费方（EvidenceView.governance）。 */
const GOVERNANCE_LABEL: Record<GovernanceDecisionView["decision"], string> = {
  ready: "READY",
  blocked: "BLOCKED",
  rollback_required: "ROLLBACK_REQUIRED",
};

export function governanceLabel(decision: string): string {
  return GOVERNANCE_LABEL[decision as GovernanceDecisionView["decision"]] ?? decision.toUpperCase();
}

/** 治理决策徽标皮肤：ready 橄榄，其余（blocked/rollback_required）赭红。展示皮肤，
 *  不是状态映射。 */
export function governanceSkin(decision: string): string {
  return decision === "ready" ? "border-olive text-olive" : "border-salmon text-salmon";
}

/** 回滚范围表的措辞与皮肤（§4.6，E-1）。**纯展示**：merged/unmerged 与
 *  withhold/revert_pull_request 都是读模型算好的枚举，这里只翻译不判定。
 *  零新颜色语义——沿用既有令牌：琥珀=还要过 CI 的 revert，橄榄=免费撤回。 */
export const ROLLBACK_STATE_LABEL: Record<RollbackRepositoryState, string> = {
  merged: "已 merge",
  unmerged: "未 merge",
};

export const ROLLBACK_STATE_SKIN: Record<RollbackRepositoryState, string> = {
  merged: "border-amber text-amber",
  unmerged: "border-line text-tx2",
};

/** 动作措辞按设计定稿 ④ 原文：「withhold 免费撤回 / revert PR 逆序第 k 步」。
 *  第 k 步由调用方拼上——k 是服务端给的 `step`，不在这里数。 */
export const ROLLBACK_ACTION_LABEL: Record<RollbackRepositoryAction, string> = {
  withhold: "withhold 免费撤回",
  revert_pull_request: "revert PR",
  none: "无可撤销项",
};

export const ROLLBACK_ACTION_SKIN: Record<RollbackRepositoryAction, string> = {
  withhold: "border-olive text-olive",
  revert_pull_request: "border-amber text-amber",
  none: "border-line text-tx3",
};

/** 入口不可用的两种原因，措辞必须不同：一个是「本轮还没有 ChangeSet」，
 *  另一个是「有 ChangeSet 但没有一个仓真的发布过」。糊成一句会让人以为
 *  是同一种空。 */
export const ROLLBACK_UNAVAILABLE_LABEL: Record<"no_change_set" | "nothing_delivered", string> = {
  no_change_set: "本轮尚未建立 ChangeSet——没有已发布的交付候选可回滚。",
  nothing_delivered: "本轮的候选一个都还没发布（既没 merge，也没开过 PR）——没有可撤销的东西。",
};

/** ISO 时间戳 → MM-DD。取不到格式时原样回显，不猜。
 *  防御同 `eventTime`（A-4）：半执行轮次的 `updated_at` 实测为 null，此前直接
 *  `.match` 抛 TypeError 把整页打成白屏。空值渲染 "—"——**这是纯格式化层的兜底，
 *  不是降级文案**：调用方若知道这个 null 有含义，该在调用处如实说出来。 */
export function dayLabel(at: string | null | undefined): string {
  if (!at) return "—";
  const m = at.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[2]}-${m[3]}` : at;
}

/** at：UTC ISO → HH:MM:SS；非 ISO 原样展示。
 *  防御：联调发现后端 repair_timeline.at 可为 null（契约写 string，已报后端），空值渲染 "—"。 */
export function eventTime(at: string | null | undefined): string {
  if (!at) return "—";
  const m = at.match(/T(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : at;
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


/** §8.7.4 重新派工：入口条件与「上次派工」标签。
 *
 *  非终态 = 读模型 §5.1 六态里还没走完的那些，与服务端 `FINAL_TASK_STATUSES`
 *  对齐——服务端重发的正是这些任务。这里**只是转述 `display_status`**，不是
 *  「卡住了」的判据：界面永远不从时间戳推结论，什么时候该重发由人决定。 */
const LIVE_TASK_STATUSES = new Set(["pending", "running", "repairing", "blocked"]);

export function isRedispatchable(task: DeliveryTaskView): boolean {
  return LIVE_TASK_STATUSES.has(task.display_status);
}

/** §8.7.4 `rerun` 范围够得着的：**结果出错了**的那些，不含「决定不做」的那些。
 *  与服务端 `_REDOABLE_TASK_STATUSES` 对齐——cancelled / superseded 属于被取消
 *  或被新版计划替换的工作，重做它等于把退役任务塞回活着的 Worker。
 *
 *  注意 `display_status` 六态里没有 cancelled/superseded 的位置（§5.1 把它们
 *  折进了 failed 一侧），所以这里读 `backend_status` 原值——这是全前端唯一一处
 *  必须读它的地方，理由就是上面这条区分在展示态里被抹掉了。 */
const RERUNNABLE_BACKEND_STATUSES = new Set(["succeeded", "failed"]);

export function isRerunnable(task: DeliveryTaskView): boolean {
  return !isRedispatchable(task) && RERUNNABLE_BACKEND_STATUSES.has(task.backend_status);
}

/** 「上次派工 HH:MM」。全部为 null 时说「无派工记录」——那不是缺数据，
 *  而是这个字段最响的一句话：这些任务压根没被派出去过。 */
export function lastDispatchLabel(tasks: DeliveryTaskView[]): string {
  const stamps = tasks.map((t) => t.last_dispatched_at).filter((at): at is string => at !== null);
  if (stamps.length === 0) return "无派工记录";
  return `上次派工 ${eventTime(stamps.sort().at(-1)!)}`;
}
