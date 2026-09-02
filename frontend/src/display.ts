import type {
  AgentRuntimeHosting,
  DecisionStatus,
  DecisionStep,
  DeliveryTaskView,
  DiscoveryTier,
  DiscoveryTierStatus,
  ExternalMemberReadinessStatus,
  GovernanceDecisionView,
  IssueListItemView,
  Phase,
  RollbackRepositoryAction,
  RollbackRepositoryState,
  RuntimeBlock,
} from "./api/contract";
import type { ProjectCheckpoint } from "./api/reviewDesk";
import type { CodeAccessLevel, HumanControlAction, HumanProjectRole } from "./api/humanControl";

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

/** A-18 未验证标记的措辞**唯一表**（同 PHASE_SKIN 的做法）。DAG 节点、证据面、
 *  授权单共用一句；四处各写各的，日后漏改一处就成了「同一件事在两屏说两种话」。
 *
 *  `blockerCount === 0` 时**只说「未验证」**，绝不写「0 条 blocker」——载荷没有结构化
 *  声明过 blocker（契约 6.12）和 agent 提出了零条 blocker 是两回事，界面不替它表态。 */
export function unverifiedMarkerLabel(blockerCount: number): string {
  return blockerCount > 0 ? `未验证 · agent 声明 ${blockerCount} 条 blocker` : "未验证";
}

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

/** 拆解模式的措辞与说明（裁决 D-2）。措辞落在**谁拆解**这件事上，不写「external」
 *  三个字——运行形态（External · Codex）是 PR 10 的 `runtime` 块的事，本页只回显
 *  拓扑里已采用的事实，两个来源不能在同一个徽标里混说。
 *
 *  `server` 是绝大多数团队的常态，**不给徽标**：满屏都是的事实不是信息。只有
 *  `leader` 显示，因为它才是「这个团队被采用成了 leader 自拆」的那一条。 */
export const TEAM_DECOMPOSITION_LABEL: Record<"server" | "leader", string> = {
  server: "平台拆解",
  leader: "Leader 自拆",
};

export const TEAM_DECOMPOSITION_HINT: Record<"server" | "leader", string> = {
  server: "平台在派 leader 任务的同一步里拆解直派（默认）",
  leader: "批次停在 leader 任务，等本团队的 Repository Leader 提交计划（materialize 采用外部 leader 的结果）",
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

/** 历史决策五步的措辞唯一表（decision-chain-v0.1 §4.1）。链内数组即 chain_order；
 *  同 (project, step) 重做 version 递增。Record 收窄到契约枚举，缺项即编译错误。 */
export const DECISION_STEP_LABEL: Record<DecisionStep, string> = {
  classification: "分类",
  confirmation: "确认",
  integration: "集成",
  task: "任务",
  pr: "PR",
};

/** 五步的**动作短语**标题（v2 改版裁决）：卡片第一视线落点。上一版标题用
 *  「分类 / 确认 / 任务」这类系统术语，读者必须问过一次才知道是什么——标题
 *  的职责是自解释，术语让位给动作短语（这步**做了什么**），原始 step 名退到
 *  折叠溯源区回显。与 DECISION_STEP_LABEL 并存：那张表服务溯源区的术语对照。 */
export const DECISION_STEP_ACTION: Record<DecisionStep, string> = {
  classification: "圈定范围",
  confirmation: "人工确认",
  integration: "排执行顺序",
  task: "拆成任务",
  pr: "提交代码",
};

/** 动作短语带运行时兜底：服务端先于前端新增 step 值时原样透出（同
 *  decisionStatusLabel 的做法——宁可显示一个陌生词，不静默归到某一步）。 */
export function decisionStepAction(step: string): string {
  return DECISION_STEP_ACTION[step as DecisionStep] ?? step;
}

/** 决策单状态的措辞唯一表（§4.1）。confirmation 由 approval.state 映射（approved→
 *  confirmed 等）；其余步落第一判词 proposed。运行时兜底原样回显，不猜新值。 */
export const DECISION_STATUS_LABEL: Record<DecisionStatus, string> = {
  proposed: "已提出",
  adjusted: "已调整",
  confirmed: "已确认",
  rejected: "已拒绝",
  changes_requested: "需修改",
  blocked: "阻塞",
  superseded: "已被取代",
  merged: "已合并",
  closed: "已关闭",
};

export function decisionStatusLabel(status: string): string {
  return DECISION_STATUS_LABEL[status as DecisionStatus] ?? status;
}

/** 决策单状态徽标皮肤（§4.1）。展示皮肤，不是状态映射：橄榄 = 正面裁决/落地，
 *  琥珀 = 在途/要改，赭红 = 拒绝/阻塞，灰 = 中性判词。零新颜色语义。 */
export const DECISION_STATUS_SKIN: Record<DecisionStatus, string> = {
  proposed: "border-line text-tx2",
  adjusted: "border-amber text-amber",
  confirmed: "border-olive text-olive",
  rejected: "border-salmon text-salmon",
  changes_requested: "border-amber text-amber",
  blocked: "border-salmon text-salmon",
  superseded: "border-line text-tx2",
  merged: "border-olive text-olive",
  closed: "border-line text-tx2",
};

export function decisionStatusSkin(status: string): string {
  return DECISION_STATUS_SKIN[status as DecisionStatus] ?? "border-line text-tx2";
}

/** actor 类型措辞（§4.1）：llm | human | service。认不出原样透出。 */
export function decisionActorLabel(type: string): string {
  return { llm: "模型", human: "人", service: "服务" }[type] ?? type;
}

/** 六个人工检查点的**展示次序**（迁移 5-1a）。这里排的是**流程先后**——
 *  确定仓库范围 → 定规格 → 执行 → 验证 → 交付，异常升级压尾（它不在主线上，
 *  是出事才走的旁路）。
 *
 *  **为什么必须自己排**：这份集合有两个来路，两个都不是人理解流程的顺序。
 *   - 拓扑端点（`GET /projects/{id}/topology`）上它是后端的 `frozenset`，
 *     序列化成数组**顺序不确定**，同一个项目每次刷新卡点次序都在跳；
 *   - 读模型 §3 那一份是 `sorted()` 的**字母序**，于是「交付」排在「执行」前面。
 *  两处都过这道排序，同一份策略在哪儿看都是一个样。 */
export const CHECKPOINT_ORDER: readonly ProjectCheckpoint[] = [
  "repository_scope",
  "specification",
  "execution",
  "validation",
  "delivery",
  "exception_escalation",
];

/** 检查点措辞唯一表（同 PHASE_SKIN 的做法）。审核台与 issue 详情页共用一份：
 *  两处各写一张表，日后漏改一处就成了「同一个卡点在两屏两个名字」。 */
export const CHECKPOINT_LABEL: Record<ProjectCheckpoint, string> = {
  repository_scope: "仓库范围",
  specification: "规格",
  execution: "执行",
  validation: "验证",
  delivery: "交付",
  exception_escalation: "异常升级",
};

/** 按一份固定次序给**来源无序**的集合定序。后端多处用 `frozenset`
 *  （`required_checkpoints`、`control_actions`），序列化成 JSON 数组后顺序不保证
 *  稳定——直接渲染会让同一份数据每次刷新排列都不同，读者会以为它变了。
 *
 *  认不出的值**原样透出**排在已知项之后，不静默丢弃：服务端先于前端新增一个枚举值时，
 *  界面上少一项比多一个陌生词危险得多（少一个卡点会让人以为那一步没人把关）。
 *  未知项内部按字母序，保证同一份输入的输出稳定。 */
export function orderByFixed(values: readonly string[], order: readonly string[]): string[] {
  const known = order.filter((item) => values.includes(item));
  const unknown = values.filter((value) => !order.includes(value)).slice().sort();
  return [...known, ...unknown];
}

export function orderCheckpoints(values: readonly string[]): string[] {
  return orderByFixed(values, CHECKPOINT_ORDER);
}

export function checkpointLabel(value: string): string {
  return CHECKPOINT_LABEL[value as ProjectCheckpoint] ?? value;
}

/** 监管策略的**界面三档**及其措辞唯一表（迁移 5-1b，设计文档 §4）。
 *
 *  **这是界面概念，不是后端取值**——后端只认 `auto` / `supervised` /
 *  `manual_controlled`，而那三个词对刚走完发现链的人毫无意义。档位在配置弹窗里由
 *  「选了哪些卡点」推导（弹窗的 `tierOf`），在策略卡片里由回读的 `execution_mode`
 *  反推（卡片的 `tierOfMode`）——两条来路，同一张措辞表。
 *
 *  **为什么在这里而不是留在弹窗里**：卡片要把「已设」显示成用户当初选的那一档，
 *  用户在弹窗里选的是「关键处我看一眼」，回到卡片必须还叫「关键处我看一眼」。
 *  抄第二份就是把 `CHECKPOINT_LABEL` 那条注释（「两处各写一张表，日后漏改一处就成了
 *  同一件事在两屏说两种话」）再犯一次。放在弹窗里导出也不行——`react/only-export-components`
 *  会因此告警，那条规则的原话就是「用一个新文件来共享常量」。 */
export type PolicyTier = "unattended" | "key_points" | "every_step";

export const POLICY_TIER_TITLE: Record<PolicyTier, string> = {
  unattended: "AI 自己干完",
  key_points: "关键处我看一眼",
  every_step: "每一步都要我点头",
};

/** 授权三要素的措辞唯一表（身份 / 代码权限 / 控制动作）。
 *
 *  **为什么在这里而不是各页自己写一份**：这三张表此前是 `IssueDetailPage` 的私有
 *  常量，只读显示（迁移 5-1a）是唯一消费方。5-1b 的配置弹窗要用**同一批词**——
 *  用户在弹窗里勾的是「批卡点」，回到详情页必须还叫「批卡点」，否则同一条授权在
 *  设它的地方和看它的地方是两个名字。抄一份到弹窗里就是把 CHECKPOINT_LABEL 那条
 *  注释（「两处各写一张表，日后漏改一处就成了同一件事在两屏说两种话」）再犯一次。
 *
 *  ⚠ 设计文档 §4.6 线框里写的是「看决定 / 批准卡点 / 要求返工」，与这里不同。
 *  以**这里**为准：5-1a 已经上线了这套词，改线框那套等于让已有界面跟着变。 */
export const ROLE_LABEL: Record<HumanProjectRole, string> = {
  organization_supervisor: "组织监督人",
  project_supervisor: "项目监督人",
  repository_supervisor: "仓库监督人",
};

export const CODE_ACCESS_LABEL: Record<CodeAccessLevel, string> = {
  none: "不读代码",
  read: "可读代码",
  write: "可写代码",
};

/** 七个控制动作的**展示次序**：先「看与批」，再「改规格」，最后三个生命周期动作。
 *  同 `CHECKPOINT_ORDER`，来源是后端 `frozenset`，顺序不确定，必须自己定序。 */
export const CONTROL_ACTION_ORDER: readonly HumanControlAction[] = [
  "view_decisions",
  "approve_checkpoint",
  "request_changes",
  "edit_specification",
  "pause_project",
  "resume_project",
  "cancel_project",
];

export const CONTROL_ACTION_LABEL: Record<HumanControlAction, string> = {
  view_decisions: "看决策",
  approve_checkpoint: "批卡点",
  request_changes: "要求修改",
  edit_specification: "改规格",
  pause_project: "暂停项目",
  resume_project: "恢复项目",
  cancel_project: "取消项目",
};

export function controlActionLabel(value: string): string {
  return CONTROL_ACTION_LABEL[value as HumanControlAction] ?? value;
}

/** 本机 CLI 成员就绪三态的措辞与皮肤唯一表（本地 CLI 页 + 物化弹窗共用一份）。
 *
 *  三色沿既有语义、不新增：橄榄绿 = 能派工，琥珀 = 租约过期但还在一个 TTL 内（重启
 *  一下就回来），赭红 = 已经不在了。措辞落在**租约**上而不是「在线/离线」——就绪是
 *  服务端按租约派生的判定，不是一次连通性探测，写成「在线」会让人以为界面 ping 过它。
 *
 *  `stale` 与 `offline` 在物化门前是同一个结果（只有 ready 过门），但对操作者不是
 *  同一件事：一个是进程还在、心跳断了，另一个是进程报过停或从没报过。 */
export const READINESS_LABEL: Record<ExternalMemberReadinessStatus, string> = {
  ready: "就绪",
  stale: "租约过期",
  offline: "离线",
};

export const READINESS_SKIN: Record<ExternalMemberReadinessStatus, string> = {
  ready: "border-olive text-olive",
  stale: "border-amber text-amber",
  offline: "border-salmon text-salmon",
};

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
 *  `opened_by_name` 是 AgentTeams 资源名（repomesh-worker-01 这类），与 §4.2 的
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

/** §4.4 运行时块的**六种呈现**。前四种都不是「有观测值」，措辞必须各不相同——
 *  把它们糊成同一句「未接入」会丢掉「探测过但打不通」与「压根没有这个资源」的区别。
 *
 *  尤其是 `unreachable`：契约明写这是**降级不是故障**（HTTP 仍 200，持久化事实照常可读），
 *  所以文案只说「运行时探测不可达」，绝不说团队/智能体坏了。
 *
 *  `external` 与前四种恰恰相反：那是**核实过的事实**，不是缺失的观测——它排在
 *  `observed` 之前，因为对一个确认不由 Controller 托管的成员，`phase` 已经没有主语。 */
export type RuntimeDisplay =
  | { kind: "probing"; label: string; hint: string }
  | { kind: "probe_failed"; label: string; hint: string }
  | { kind: "unreachable"; label: string; hint: string }
  | { kind: "absent"; label: string; hint: string }
  | { kind: "external"; label: string; hint: string }
  | { kind: "observed"; label: string; hint: string };

/** 按**结构**收窄而不绑定页面类型：团队页的 `TeamRuntimeFields` 没有 `kind`
 *  （list_teams 只探测 team 资源，per-member 的托管方式那里无源），所以 `kind`
 *  可选——缺席即「这个调用方问不出托管方式」，与花名册的 `kind: null` 同义。 */
export function runtimeDisplay(
  phase: RuntimePhase,
  runtime: RuntimeBlock<{ phase: string | null; kind?: AgentRuntimeHosting | null }>,
): RuntimeDisplay {
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
  // 托管方式先于阶段：确认 external 的成员没有容器阶段可言，再往下读 phase
  // 就是在替一个不存在的容器编状态。文案不提任何具体 CLI——平台核实过的只有
  // 「容器不归 Controller 管」这一件事，CLI 种类它压根不观测。
  if (runtime.kind === "external") {
    return {
      kind: "external",
      label: "External",
      hint: "containerManaged:false 已由平台向 Controller 核实；本机 CLI 经 Bridge 接入，平台不观测 CLI 种类",
    };
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
