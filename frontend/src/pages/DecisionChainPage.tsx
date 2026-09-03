/** 历史决策页（#/decision-chains，decision-chain-v0.1 §6）。
 *
 *  审计人员的两个入口，与方案确认时一致：
 *   - **语义检索**：输入自然语言探针 → 跨组织按文本搜历史决策（§6.5 扩展，
 *     `score` = 余弦相似度；无 structural 回退，embedding 未配置是可见的 503）；
 *   - **需求定位**：输入 issue 列表里可见的信息（标题关键词 / #短id / 完整 UUID）
 *     → 定位项目并追溯完整决策链（§6.1，organization 未知时省略即跨组织）+
 *     相似历史（§6.5，structural 同仓最近 / semantic 余弦）。
 *
 *  链详情按 v3 定稿渲染（docs/design/decision-chain-v3-mockup.html）：
 *   - 链头：需求原文 + 终态徽章 + 审计 ids 行（项目 / 需求快照 / 计划版本）；
 *   - 五步进度条：实心✓=有记录 / ⚠=缺口（§7 legacy_gaps）/ 空心=未到达，
 *     可点击跳到对应卡；
 *   - 每步一张生效卡（版本聚合）：classification/confirmation/integration 取
 *     最新版本作正文，历史版本收进卡内「版本记录」时间线（已被取代可展开完整
 *     内容）；task/pr 是并列实例，逐行列出不去重；
 *   - 缺口/未到达用虚线幽灵卡在序列中占位，如实标注「数据如此、非展示缺漏」。
 *
 *  replay 模式（?source=replay）渲染 data/decisionChain.ts 的演示剧本，进页面
 *  自动跑一次示范语义检索；live 模式打真实端点（Bearer agent_action_token）。
 *  数据源标注与其余页同款（?source=live 打真实读模型）。 */
import { Fragment, useCallback, useEffect, useState } from "react";
import type {
  DecisionChainView,
  DecisionNodeView,
  DecisionStep,
  SimilarDecisionView,
  SimilarDecisionsView,
} from "../api/contract";
import {
  decisionChainSourceMode,
  fetchDecisionChain,
  fetchSimilarDecisions,
  locateProjectCandidates,
  parseProjectInput,
  refreshDecisionEmbeddings,
  searchSemanticDecisions,
  type DecisionProjectCandidate,
} from "../api/decisionChain";
import {
  DECISION_PROJECT_META,
  DECISION_REPO_NAMES,
  MAIN_PROJECT_ID,
  resolveReplayProjectId,
} from "../data/decisionChain";
import {
  dayLabel,
  decisionActorLabel,
  decisionStepAction,
  decisionStatusLabel,
  decisionStatusSkin,
  errText,
  eventTime,
  shortId,
} from "../display";
import { LoadingLine } from "../components/StatusBlocks";

type SearchMode = "semantic" | "id";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 仓库 id → 展示名：夹具世界有映射；live 读模型只给 id → UUID 截短展示，
 *  非 UUID（如本来就是 "saleor-core" 这样的仓库名）原样透出，不截断可读名。 */
function repoLabel(repositoryId: string): string {
  if (DECISION_REPO_NAMES[repositoryId]) return DECISION_REPO_NAMES[repositoryId];
  return UUID_RE.test(repositoryId) ? shortId(repositoryId) : repositoryId;
}

const chipCls = "rounded border px-1.5 py-0.5 text-[10px]";
const inputCls =
  "rounded border border-line bg-panel px-2 py-1 text-[11px] text-cream placeholder:text-tx3/60 outline-none transition-colors focus:border-amber-hi/60";
const buttonCls =
  "rounded border border-line px-2.5 py-1 text-[11px] text-tx2 transition-colors hover:border-amber-hi/60 hover:text-amber-hi disabled:cursor-not-allowed disabled:opacity-40";

// ══════════════ 确认步的两种数据形状（兼容读，不编造） ══════════════

/** 谁拍板的：live/seed 写 decided_by 角色名（如 "product-owner"）；
 *  夹具与后端契约写 decided_by_agent_id（UUID，截短展示）。 */
function decidedByLabel(approval: Record<string, unknown>): string {
  if (typeof approval.decided_by === "string" && approval.decided_by) return approval.decided_by;
  if (typeof approval.decided_by_agent_id === "string" && approval.decided_by_agent_id) {
    return `agent ${shortId(approval.decided_by_agent_id)}`;
  }
  return "决策人";
}

type ApprovalAdjustment =
  | { kind: "condition"; text: string }
  | { kind: "tier"; repository: string; from: string; to: string };

/** 批准时的附加约束，两种形状：
 *  - live/seed：字符串数组 = 批准附加条件（如「仅支持发货后 30 天内部分退款」）；
 *  - 夹具/契约：{repository, from, to} 对象数组 = 确认时改档（saleor-docs: maybe → required）。 */
function adjustmentItems(p: Record<string, unknown>): ApprovalAdjustment[] {
  if (!Array.isArray(p.adjustments)) return [];
  return (p.adjustments as unknown[]).flatMap((raw): ApprovalAdjustment[] => {
    if (typeof raw === "string") return raw ? [{ kind: "condition", text: raw }] : [];
    if (raw && typeof raw === "object") {
      const a = raw as Record<string, unknown>;
      if (typeof a.repository === "string") {
        return [
          {
            kind: "tier",
            repository: repoLabel(a.repository),
            from: String(a.from ?? "—"),
            to: String(a.to ?? "—"),
          },
        ];
      }
    }
    return [];
  });
}

// ══════════════ 命中条目的摘要行（列表里没有标题字段，从 payload 摘一句） ══════════════

function hitSummaryLine(hit: SimilarDecisionView): string {
  const p = hit.payload_summary;
  switch (hit.step) {
    case "task": {
      const t = p.title;
      return typeof t === "string" ? t : "任务已规划";
    }
    case "pr": {
      const n = p.pull_request_number;
      const r = typeof p.repository_id === "string" ? repoLabel(p.repository_id) : "";
      return `PR #${String(n ?? "—")} · ${r}`;
    }
    case "integration": {
      const b = p.execution_batches;
      return Array.isArray(b) ? `${b.length} 批执行计划` : "集成已规划";
    }
    case "confirmation": {
      const a = p.approval;
      const approval = a && typeof a === "object" ? (a as Record<string, unknown>) : {};
      const reason = typeof approval.reason === "string" ? approval.reason : null;
      if (reason) return reason;
      const state = typeof approval.state === "string" ? approval.state : "";
      if (state === "approved") {
        const adjCount = adjustmentItems(p).length;
        return adjCount > 0
          ? `${decidedByLabel(approval)} 批准，附加 ${adjCount} 项条件`
          : `${decidedByLabel(approval)} 批准`;
      }
      return "已确认";
    }
    default: {
      const r = p.required;
      return Array.isArray(r) && r.length > 0 ? `必改：${(r as string[]).join(" / ")}` : "已圈定范围";
    }
  }
}

/** 相似度条：宽 = score。score 恒在 [0,1]（余弦），0.28 以下是「没怎么搭上」。 */
function ScoreBar({ score }: { score: number }) {
  const pct = Math.max(0, Math.min(100, Math.round(score * 100)));
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1 w-16 overflow-hidden rounded-full bg-line">
        <div className="h-full rounded-full bg-amber" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] tabular-nums text-tx2">{score.toFixed(2)}</span>
    </div>
  );
}

// ══════════════ 步骤卡正文（payload 只带指针级摘要，按步分派渲染） ══════════════

function payloadOf(node: DecisionNodeView): Record<string, unknown> {
  return (node.payload_summary ?? {}) as Record<string, unknown>;
}

/** unknown → string[]：只收非空字符串，形状不对不编造。 */
function strArr(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x.length > 0) : [];
}

/** 圈定范围：必改展开成清单（读者要逐项核对的），待定/不做折叠进 details。 */
function ClassificationBody({ p }: { p: Record<string, unknown> }) {
  const required = strArr(p.required);
  const maybe = strArr(p.maybe);
  const excluded = strArr(p.excluded);
  if (required.length + maybe.length + excluded.length === 0) return null;
  return (
    <div>
      {required.length > 0 && (
        <div>
          <span className="rounded border border-olive/50 px-2 py-0.5 font-mono text-[11px] font-bold text-olive">
            必改 {required.length}
          </span>
          <ul className="mt-1.5">
            {required.map((x) => (
              <li key={x} className="py-0.5 text-[12.5px] leading-relaxed text-tx">
                <span className="mr-1.5">·</span>
                {x}
              </li>
            ))}
          </ul>
        </div>
      )}
      {(maybe.length > 0 || excluded.length > 0) && (
        <details className="mt-2">
          <summary className="cursor-pointer select-none font-mono text-[11.5px] text-tx2 hover:text-tx">
            待定 {maybe.length} · 不做 {excluded.length} ▸
          </summary>
          <div className="mt-2 space-y-2.5">
            {maybe.length > 0 && (
              <div>
                <span className="rounded border border-amber/50 px-2 py-0.5 font-mono text-[11px] font-bold text-amber">
                  待定
                </span>
                <ul className="mt-1.5">
                  {maybe.map((x) => (
                    <li key={x} className="py-0.5 text-[12.5px] leading-relaxed text-tx2">
                      <span className="mr-1.5">·</span>
                      {x}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {excluded.length > 0 && (
              <div>
                <span className="rounded border border-line px-2 py-0.5 font-mono text-[11px] font-bold text-tx3">
                  不做
                </span>
                <ul className="mt-1.5">
                  {excluded.map((x) => (
                    <li key={x} className="py-0.5 text-[12.5px] leading-relaxed text-tx3">
                      <span className="mr-1.5">·</span>
                      {x}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  );
}

/** 确认结论词 + 结果点颜色。approval.state 优先，缺失时按 node.status 同义映射。 */
function confirmationResult(state: string, status: DecisionNodeView["status"]): { label: string; dot: string } {
  if (state === "approved" || state === "confirmed" || status === "confirmed") return { label: "批准", dot: "bg-olive" };
  if (state === "rejected" || status === "rejected") return { label: "驳回", dot: "bg-salmon" };
  if (state === "changes_requested" || status === "changes_requested") return { label: "按要求修改", dot: "bg-amber" };
  return { label: decisionStatusLabel(status), dot: "bg-line" };
}

/** 人工确认正文：结果行（点+结论词+拍板人）→ 批准理由（引用块）→ 附加约束（虚线 chips）。 */
function ConfirmationBody({ node }: { node: DecisionNodeView }) {
  const p = payloadOf(node);
  const approval = (p.approval ?? {}) as Record<string, unknown>;
  const state = typeof approval.state === "string" ? approval.state : "";
  const reason = typeof approval.reason === "string" && approval.reason ? approval.reason : null;
  const adjustments = adjustmentItems(p);
  const result = confirmationResult(state, node.status);
  const adjustLabel = result.label === "批准" ? "批准时附加" : result.label === "按要求修改" ? "随批注调整" : "附加约束";
  return (
    <div>
      <div className="my-1 flex items-center gap-2">
        <span className={`h-2 w-2 flex-none rounded-full ${result.dot}`} />
        <span className="text-[14px] font-semibold text-cream">{result.label}</span>
        <span className="text-[11px] text-tx3">{decidedByLabel(approval)}</span>
      </div>
      {reason && (
        <div className="mt-1.5">
          <span className="microlabel">批准理由</span>
          <p className="mt-1 border-l-2 border-line pl-3 text-[12.5px] leading-[1.75] text-tx">{reason}</p>
        </div>
      )}
      {adjustments.length > 0 && (
        <div className="mt-3">
          <span className="microlabel">{adjustLabel}</span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {adjustments.map((a, i) => (
              <span
                key={i}
                className="rounded border border-dashed border-amber/40 bg-amber/10 px-2.5 py-[3px] font-mono text-[11.5px] text-amber-hi"
              >
                {a.kind === "condition" ? a.text : `${a.repository}：${a.from} → ${a.to}`}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** 排执行顺序正文：每条契约一行——提供方 定义 接口 → 消费方 消费（v3 契约行）。 */
/** 集成单的两种线上形状：契约既可能是对象 `{provider, interface, consumers}`，也可能是投影
 *  压成的字符串 `provider->consumer:接口`；批次既可能是 `string[][]`，也可能是
 *  `{index, repository_ids}[]`。两种都收，缺哪个字段就空着，不让一张卡把整页拖垮。 */
function normalizeContract(c: unknown): Record<string, unknown> {
  if (c && typeof c === "object") return c as Record<string, unknown>;
  if (typeof c === "string") {
    const [edge, iface] = c.split(/:(.*)/s);
    const [provider, consumer] = edge.split("->");
    return {
      provider: provider?.trim() || null,
      interface: iface?.trim() || edge,
      consumers: consumer ? [consumer.trim()] : [],
    };
  }
  return {};
}

function normalizeBatch(b: unknown): string[] {
  if (Array.isArray(b)) return b.filter((x): x is string => typeof x === "string");
  if (b && typeof b === "object") return strArr((b as Record<string, unknown>).repository_ids);
  return [];
}

function IntegrationBody({ p }: { p: Record<string, unknown> }) {
  const contracts = Array.isArray(p.contracts) ? (p.contracts as unknown[]).map(normalizeContract) : [];
  const batches = Array.isArray(p.execution_batches)
    ? (p.execution_batches as unknown[]).map(normalizeBatch)
    : [];
  return (
    <div>
      {contracts.map((c, i) => {
        const provider = typeof c.provider === "string" ? c.provider : null;
        const iface = typeof c.interface === "string" ? c.interface : "—";
        const consumers = strArr(c.consumers);
        return (
          <div key={i} className="my-1.5 flex flex-wrap items-center gap-2">
            {provider && (
              <span className="rounded border border-line bg-panel-2 px-[9px] py-0.5 font-mono text-[12px] text-tx">
                {repoLabel(provider)}
              </span>
            )}
            <span className="microlabel">定义</span>
            <span className="rounded border border-amber/35 bg-amber/10 px-[9px] py-0.5 font-mono text-[12px] text-amber-hi">
              {iface}
            </span>
            <span className="text-tx3">→</span>
            {consumers.map((x) => (
              <span key={x} className="rounded border border-line bg-panel-2 px-[9px] py-0.5 font-mono text-[12px] text-tx">
                {repoLabel(x)}
              </span>
            ))}
            {consumers.length > 0 && <span className="microlabel">消费</span>}
          </div>
        );
      })}
      {batches.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="microlabel">执行批次</span>
          {batches.map((batch, i) => (
            <span key={i} className="rounded border border-line bg-panel-2 px-2 py-0.5 font-mono text-[11.5px] text-tx2">
              批次 {i + 1}：{batch.map(repoLabel).join(" / ")}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** 拆成任务正文：task 是并列实例（每任务一张单），逐行列出不去重；
 *  task_ids 数组优先（live/seed），单值 task_id 兜底（旧夹具）。 */
function TaskBody({ nodes }: { nodes: DecisionNodeView[] }) {
  return (
    <div>
      {nodes.map((node, i) => {
        const p = payloadOf(node);
        const ids = strArr(p.task_ids);
        if (ids.length === 0 && typeof p.task_id === "string" && p.task_id) ids.push(p.task_id);
        return (
          <div key={node.decision_id} className={i > 0 ? "mt-3.5 border-t border-dashed border-line pt-3.5" : ""}>
            {typeof p.title === "string" && p.title && (
              <p className="mt-0.5 text-[14.5px] font-semibold leading-snug text-cream">{p.title}</p>
            )}
            {ids.length > 0 && (
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <span className="microlabel">任务 {ids.length} 个</span>
                {ids.map((t) => (
                  <span key={t} className="rounded border border-line bg-panel-2 px-2 py-0.5 font-mono text-[11.5px] text-tx2">
                    {t}
                  </span>
                ))}
              </div>
            )}
            {typeof p.parent_task_id === "string" && p.parent_task_id && (
              <p className="mt-1.5 font-mono text-[10.5px] text-tx3">父任务 {p.parent_task_id}</p>
            )}
            <ProvBlock node={node} />
          </div>
        );
      })}
    </div>
  );
}

/** PR 状态徽章：合入=橄榄 / 关闭（含被取代）=灰 / 其余=琥珀待合入。 */
function prStateLabel(status: string): { label: string; cls: string } {
  if (status === "merged") return { label: "已合入", cls: "border-olive/55 bg-olive/10 text-olive" };
  if (status === "closed" || status === "superseded") return { label: "已关闭", cls: "border-line text-tx2" };
  return { label: "待合入", cls: "border-amber/55 bg-amber/10 text-amber" };
}

/** 提交代码正文：状态徽章 + PR 链接（可点去远端），每仓每单一行。 */
function PrBody({ nodes }: { nodes: DecisionNodeView[] }) {
  return (
    <div>
      {nodes.map((node, i) => {
        const p = payloadOf(node);
        const state = prStateLabel(node.status);
        const url = typeof p.pull_request_url === "string" && p.pull_request_url ? p.pull_request_url : null;
        const num = p.pull_request_number;
        return (
          <div key={node.decision_id} className={i > 0 ? "mt-3.5 border-t border-dashed border-line pt-3.5" : ""}>
            <div className="mt-0.5 flex flex-wrap items-center gap-2.5">
              <span className={`rounded border px-2.5 py-[3px] font-mono text-[12px] font-bold ${state.cls}`}>
                {state.label}
              </span>
              <span className="font-mono text-[12px] text-amber">
                PR{" "}
                {url ? (
                  <a href={url} target="_blank" rel="noreferrer" className="hover:underline">
                    #{String(num ?? "—")}
                  </a>
                ) : (
                  `#${String(num ?? "—")}`
                )}
              </span>
              {typeof p.change_set_id === "string" && p.change_set_id && (
                <span className="font-mono text-[10.5px] text-tx3">change_set {p.change_set_id}</span>
              )}
            </div>
            <ProvBlock node={node} />
          </div>
        );
      })}
    </div>
  );
}

// ══════════════ 链级视图模型：五步固定序列 → 每步一个槽位（v3 定稿） ══════════════

const STEP_ORDER: DecisionStep[] = ["classification", "confirmation", "integration", "task", "pr"];

/** 版本递增步：同一步重做只升 version，聚合一张生效卡 + 卡内版本记录；
 *  task/pr 是并列实例（每任务/每仓各一张单），逐行列出，绝不去重。 */
const VERSIONED_STEPS = new Set<DecisionStep>(["classification", "confirmation", "integration"]);

interface StepSlot {
  step: DecisionStep;
  /** done=有记录 / gap=缺口（legacy_gaps，或后步已出现却缺本步）/ todo=未到达 */
  state: "done" | "gap" | "todo";
  /** 版本步按 version 升序（末位=生效版）；实例步按业务时间升序 */
  nodes: DecisionNodeView[];
  date: string | null;
}

/** 五步槽位：进度条与卡片序列共用同一视图模型，两边永远一致。 */
function chainStepSlots(trace: DecisionChainView): StepSlot[] {
  const gaps = new Set(trace.legacy_gaps);
  let maxReached = -1;
  STEP_ORDER.forEach((s, i) => {
    if (trace.nodes.some((n) => n.step === s)) maxReached = i;
  });
  return STEP_ORDER.map((step, i) => {
    const nodes = trace.nodes.filter((n) => n.step === step);
    if (VERSIONED_STEPS.has(step)) nodes.sort((a, b) => a.version - b.version);
    else nodes.sort((a, b) => a.business_time.localeCompare(b.business_time));
    if (nodes.length > 0) {
      return { step, state: "done" as const, nodes, date: dayLabel(nodes[nodes.length - 1].business_time) };
    }
    const isGap = gaps.has(step) || i < maxReached; // 缺尾不算缺口（契约口径）
    return { step, state: isGap ? ("gap" as const) : ("todo" as const), nodes, date: null };
  });
}

/** approval.state 原值（live/夹具都写在这；node.status 是它的映射，双读不编造）。 */
function approvalStateOf(node: DecisionNodeView): string {
  const approval = (payloadOf(node).approval ?? {}) as Record<string, unknown>;
  return typeof approval.state === "string" ? approval.state : "";
}

/** 链级终态：读者点开页面的第一个问题「这件事最后怎么样了」。
 *  只按已有决策单判定，**绝不编造「进行中」**——没有 PR 记录就如实说未提交。 */
function chainOutcome(nodes: DecisionNodeView[]): { label: string; skin: string } {
  const confirmations = nodes.filter((n) => n.step === "confirmation").sort((a, b) => a.version - b.version);
  const confirmation = confirmations[confirmations.length - 1];
  if (confirmation) {
    const state = approvalStateOf(confirmation);
    if (confirmation.status === "rejected" || state === "rejected") {
      return { label: "未通过人工确认", skin: "border-salmon/55 bg-salmon/10 text-salmon" };
    }
    if (confirmation.status === "changes_requested" || state === "changes_requested") {
      return { label: "等待按要求修改", skin: "border-amber/55 bg-amber/10 text-amber" };
    }
  }
  const prs = nodes.filter((n) => n.step === "pr");
  if (prs.length === 0) return { label: "未提交代码", skin: "border-line text-tx2" };
  const merged = prs.find((n) => n.status === "merged");
  if (merged) {
    const num = payloadOf(merged).pull_request_number;
    return { label: num != null ? `已落地 · PR #${String(num)}` : "已落地", skin: "border-olive/55 bg-olive/10 text-olive" };
  }
  return { label: "代码待合入", skin: "border-amber/55 bg-amber/10 text-amber" };
}

/** 拍板人徽章：人=琥珀（确认步从 payload 取角色名），模型/服务=蓝灰。
 *  「谁拍的板」是读者五问之一，放卡片右上角常驻，不折叠。 */
function actorBadge(node: DecisionNodeView): { cls: string; label: string } {
  const human = "border-amber-hi/55 bg-amber/10 text-amber-hi";
  const machine = "border-bluegray/45 text-bluegray";
  if (node.step === "confirmation") {
    const approval = (payloadOf(node).approval ?? {}) as Record<string, unknown>;
    const by = decidedByLabel(approval);
    if (by !== "决策人") return { cls: human, label: `人 · ${by}` };
  }
  if (node.actor.type === "human") return { cls: human, label: "人" };
  if (node.actor.type === "service") return { cls: machine, label: "服务同步" };
  return { cls: machine, label: node.actor.type === "llm" ? "模型决策" : decisionActorLabel(node.actor.type) };
}

/** 版本记录行的一句头：确认步带结论词 + 理由状态（补录/有/未留），其余步用状态词。 */
function versionHead(node: DecisionNodeView, isCurrent: boolean, prev: DecisionNodeView | null): string {
  if (node.step === "confirmation") {
    const approval = (payloadOf(node).approval ?? {}) as Record<string, unknown>;
    const state = typeof approval.state === "string" ? approval.state : "";
    const word = confirmationResult(state, node.status).label;
    const hasReason = typeof approval.reason === "string" && approval.reason.length > 0;
    const prevHadNoReason =
      !prev ||
      !(() => {
        const a = (payloadOf(prev).approval ?? {}) as Record<string, unknown>;
        return typeof a.reason === "string" && a.reason.length > 0;
      })();
    if (isCurrent && hasReason && prevHadNoReason) return `${word} · 补录批准理由`;
    return hasReason ? `${word} · 有批准理由` : `${word} · 未留理由`;
  }
  return decisionStatusLabel(node.status);
}

/** 旧版本展开用的 payload 摘要（折叠在「v{N} 完整内容」里，指针级如实取材）。 */
function payloadDigest(node: DecisionNodeView): string[] {
  const lines: string[] = [];
  const p = payloadOf(node);
  if (node.step === "confirmation") {
    const approval = (p.approval ?? {}) as Record<string, unknown>;
    const state = typeof approval.state === "string" ? approval.state : "";
    lines.push(`state=${state || node.status} · decided_by=${decidedByLabel(approval)}`);
    const adjustments = adjustmentItems(p).map((a) => (a.kind === "condition" ? a.text : `${a.repository}: ${a.from} → ${a.to}`));
    lines.push(`附加条件: ${adjustments.length > 0 ? adjustments.join(" / ") : "（无）"}`);
    const reason = typeof approval.reason === "string" && approval.reason ? approval.reason : null;
    lines.push(`批准理由: ${reason ?? "（未记录）"}`);
  } else if (node.step === "classification") {
    lines.push(`必改: ${strArr(p.required).join(" / ") || "（无）"}`);
    lines.push(`待定: ${strArr(p.maybe).join(" / ") || "（无）"}`);
    lines.push(`不做: ${strArr(p.excluded).join(" / ") || "（无）"}`);
  } else if (node.step === "integration") {
    const contracts = Array.isArray(p.contracts) ? (p.contracts as Record<string, unknown>[]) : [];
    if (contracts.length === 0) lines.push("契约: （无）");
    contracts.forEach((c) => {
      const provider = typeof c.provider === "string" ? c.provider : "—";
      const iface = typeof c.interface === "string" ? c.interface : "—";
      const consumers = strArr(c.consumers);
      lines.push(`契约: ${provider} 定义 ${iface}${consumers.length > 0 ? ` → ${consumers.join(" / ")} 消费` : ""}`);
    });
  }
  lines.push(`event=${node.event_type} · ${node.business_time.slice(0, 10)}`);
  lines.push(`decision_id=${node.decision_id}`);
  return lines;
}

/** ISO 时间 → 人读 UTC（溯源区专用，保留全量精度）。 */
function utcLabel(t: string): string {
  return t.endsWith("Z") ? `${t.slice(0, -1).replace("T", " ")} UTC` : t.replace("T", " ");
}

// ══════════════ 相似/语义命中列表 ══════════════

/** 防御性去重：检索单位是需求（project_id）——即使后端契约被破坏，同一需求
 *  也只许出现一张卡（保留相似度更高的一条），绝不把一次需求的几个环节拆开展示。 */
function dedupeByProject(hits: SimilarDecisionView[]): SimilarDecisionView[] {
  const seen = new Map<string, SimilarDecisionView>();
  for (const hit of hits) {
    const prev = seen.get(hit.project_id);
    if (prev === undefined || (hit.score ?? -1) > (prev.score ?? -1)) {
      seen.set(hit.project_id, hit);
    }
  }
  return [...seen.values()];
}

function HitCard({
  hit,
  mode,
  onOpen,
}: {
  hit: SimilarDecisionView;
  mode: "semantic" | "structural";
  onOpen: (projectId: string) => void;
}) {
  const requirement = hit.requirement_text?.trim() || null;
  const scoreBar =
    mode === "semantic" && hit.score !== null ? <ScoreBar score={hit.score} /> : null;
  return (
    <button
      onClick={() => onOpen(hit.project_id)}
      className="w-full rounded-hard border border-line bg-panel px-4 py-3 text-left transition-colors hover:border-amber-hi/50"
    >
      {requirement !== null ? (
        /* 需求级卡头：一句话需求根句 + 右侧相似度与需求短 id */
        <div className="flex flex-wrap items-start gap-x-2 gap-y-1">
          <span className="min-w-0 flex-1 basis-56 text-[12.5px] font-semibold leading-snug text-cream">
            {requirement}
          </span>
          {scoreBar}
          <span className="font-mono text-[10px] text-tx3">{shortId(hit.project_id)}</span>
        </div>
      ) : (
        /* 诚实缺口：该项目没有需求快照 → 回退决策单级卡头，不从 payload 碎片杜撰标题 */
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[12px] font-semibold text-cream">{decisionStepAction(hit.step)}</span>
          {hit.version > 1 && (
            <span className={`${chipCls} border-line text-tx3`}>v{hit.version}</span>
          )}
          <span className={`${chipCls} ${decisionStatusSkin(hit.status)}`}>
            {decisionStatusLabel(hit.status)}
          </span>
          {scoreBar}
          <span className="ml-auto text-[10.5px] tabular-nums text-tx3">
            {dayLabel(hit.business_time)} {eventTime(hit.business_time)}
          </span>
        </div>
      )}
      <p className="mt-1 text-[11px] leading-relaxed text-tx2">
        {requirement !== null ? (
          <>
            命中依据：{decisionStepAction(hit.step)}
            {hit.version > 1 ? ` v${hit.version}` : ""} · {decisionStatusLabel(hit.status)}
            {mode === "semantic" ? "（与查询最相似的决策单）" : ""} ·{" "}
            {dayLabel(hit.business_time)} {eventTime(hit.business_time)} ·{" "}
            <span className="text-tx3">点击展开该需求的完整决策链</span>
          </>
        ) : (
          hitSummaryLine(hit)
        )}
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        {hit.affected_repository_ids.map((r) => (
          <span key={r} className={`${chipCls} border-line text-tx2`}>
            {repoLabel(r)}
          </span>
        ))}
        {requirement === null && (
          <span className="ml-auto text-[10px] text-tx3">{shortId(hit.project_id)}</span>
        )}
      </div>
    </button>
  );
}

// ══════════════ 五步卡片序列（v3 定稿：每步一卡，版本聚合，缺口幽灵卡） ══════════════

/** 溯源折叠层（默认收起）：全量审计 id、UTC 时间、事件类型、上游指针与证据指针。
 *  版本步的当前版与 task/pr 实例行共用；current 标记是否当前生效。 */
function ProvBlock({ node, current }: { node: DecisionNodeView; current?: boolean }) {
  const p = payloadOf(node);
  const taskIds = strArr(p.task_ids).length > 0 ? strArr(p.task_ids) : strArr(p.task_id);
  const changeSet = typeof p.change_set_id === "string" ? p.change_set_id : null;
  const rows: Array<[string, string]> = [
    ["系统步骤", `${node.step} · v${node.version}${current ? "（生效）" : ""}`],
    ["decision_id", node.decision_id],
    ["event_id", node.event_id],
    ["业务时间", utcLabel(node.business_time)],
    ["入库时间", utcLabel(node.recorded_at)],
    ["事件类型", node.event_type],
  ];
  if (node.actor.agent_id) rows.push(["agent", node.actor.agent_id]);
  if (node.upstream_ref) rows.push(["上游决策单", node.upstream_ref]);
  if (taskIds.length > 0) rows.push(["关联任务", taskIds.join(" / ")]);
  if (changeSet) rows.push(["change_set", changeSet]);
  Object.entries(node.evidence_refs ?? {}).forEach(([key, refs]) => {
    rows.push([`证据·${key}`, Array.isArray(refs) && refs.length > 0 ? refs.join(" / ") : "—"]);
  });
  return (
    <details className="mt-3">
      <summary className="microlabel cursor-pointer select-none hover:text-tx">溯源 · 原始事件与证据</summary>
      <div className="mt-2 rounded-hard border border-line bg-ink-deep px-3.5 py-2.5 font-mono text-[11px] leading-[1.9] text-tx3">
        {rows.map(([k, v]) => (
          <div key={k} className="break-all">
            <span className="inline-block w-[108px] align-top text-tx2">{k}</span>
            {v}
          </div>
        ))}
      </div>
    </details>
  );
}

/** 版本记录：版本步的旧版本时间线。当前版正文已在卡头，这里只放差异头 + 可展开全文
 *  （v1 被谁取代、当前版补录了什么，一目了然——两张人工确认卡分不清的问题就解在这里）。 */
function VersionHistory({ versions }: { versions: DecisionNodeView[] }) {
  if (versions.length < 2) return null;
  return (
    <div className="mt-3.5 border-t border-dashed border-line pt-2.5">
      <span className="microlabel">版本记录</span>
      {versions.map((node, i) => {
        const isCurrent = i === versions.length - 1;
        const prev = i > 0 ? versions[i - 1] : null;
        return (
          <div key={node.decision_id} className="mt-2 flex gap-2.5">
            <div className="flex flex-col items-center">
              <span
                className={`mt-[5px] h-2.5 w-2.5 flex-none rounded-full border-2 ${
                  isCurrent ? "border-amber-hi bg-amber-hi" : "border-line"
                }`}
              />
              {!isCurrent && <span className="min-h-2 w-0.5 flex-1 bg-line" />}
            </div>
            <div className={`min-w-0 flex-1 ${isCurrent ? "" : "pb-2.5"}`}>
              <div className="text-[13px] leading-snug text-tx">
                <span className="mr-2 font-mono text-[11px] text-tx2">{dayLabel(node.business_time)}</span>
                v{node.version} · {versionHead(node, isCurrent, prev)}
                <span
                  className={`ml-2 rounded border px-[7px] py-px font-mono text-[10px] font-bold ${
                    isCurrent ? "border-olive/50 text-olive" : "border-line text-tx3"
                  }`}
                >
                  {isCurrent ? "当前生效" : "已被取代"}
                </span>
              </div>
              {!isCurrent && (
                <details className="mt-1">
                  <summary className="cursor-pointer select-none font-mono text-[11px] text-tx3 hover:text-tx2">
                    v{node.version} 完整内容 ▸
                  </summary>
                  <pre className="mt-1.5 whitespace-pre-wrap rounded-hard border border-line bg-ink-deep px-3 py-2.5 font-mono text-[11px] leading-[1.8] text-tx2">
                    {payloadDigest(node).join("\n")}
                  </pre>
                </details>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** 实心步骤卡：卡头（序号+动作短语+拍板人+日期）→ 正文（按步分派）→ 版本记录 → 影响仓库 → 溯源。 */
function StepCard({ slot, stepIndex }: { slot: StepSlot; stepIndex: number }) {
  const { step, nodes } = slot;
  const current = nodes[nodes.length - 1];
  const badge = actorBadge(current);
  const repos = Array.from(new Set(nodes.flatMap((n) => n.affected_repository_ids)));
  return (
    <section id={`chain-step-${stepIndex}`} className="mt-4 rounded-hard border border-line bg-panel">
      <div className="flex items-start justify-between gap-3 px-[18px] pt-3.5">
        <div className="flex items-baseline gap-2.5">
          <span className="font-mono text-[13px] font-bold text-amber">{stepIndex + 1}</span>
          <span className="text-[15.5px] font-semibold text-cream">{decisionStepAction(step)}</span>
        </div>
        <div className="flex flex-none flex-col items-end gap-1">
          <span
            className={`whitespace-nowrap rounded border px-[9px] py-[3px] font-mono text-[10.5px] font-bold tracking-[0.04em] ${badge.cls}`}
          >
            {badge.label}
          </span>
          {slot.date && <span className="font-mono text-[11px] text-tx2">{slot.date}</span>}
        </div>
      </div>
      <div className="px-[18px] pb-4 pt-2.5">
        {step === "classification" && <ClassificationBody p={payloadOf(current)} />}
        {step === "confirmation" && <ConfirmationBody node={current} />}
        {step === "integration" && <IntegrationBody p={payloadOf(current)} />}
        {step === "task" && <TaskBody nodes={nodes} />}
        {step === "pr" && <PrBody nodes={nodes} />}
        {VERSIONED_STEPS.has(step) && <VersionHistory versions={nodes} />}
        {repos.length > 0 && (
          <div className="mt-3.5">
            <span className="microlabel">影响仓库</span>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {repos.map((r) => (
                <span
                  key={r}
                  className="rounded border border-line bg-panel-2 px-[9px] py-[3px] font-mono text-[11.5px] text-tx"
                >
                  {repoLabel(r)}
                </span>
              ))}
            </div>
          </div>
        )}
        {VERSIONED_STEPS.has(step) && <ProvBlock node={current} current />}
      </div>
    </section>
  );
}

/** 幽灵卡：缺口步（⚠）与未到达步（虚线空心），在五步序列中如实占位，不补叙事。 */
function GhostCard({ slot, stepIndex }: { slot: StepSlot; stepIndex: number }) {
  const gap = slot.state === "gap";
  return (
    <section
      id={`chain-step-${stepIndex}`}
      className={`mt-4 rounded-hard border border-dashed px-[18px] py-[13px] ${gap ? "border-salmon/55" : "border-line"}`}
    >
      <p className={`text-[14px] font-semibold ${gap ? "text-salmon" : "text-tx2"}`}>
        <span className="opacity-85">{stepIndex + 1}.</span> {gap ? "⚠ " : ""}
        {decisionStepAction(slot.step)} · 无记录
      </p>
      <p className={`mt-1 text-[12.5px] leading-relaxed ${gap ? "text-tx2" : "text-tx3"}`}>
        {gap ? "这一步没有留下决策记录；前后步骤都在（数据如此，非展示缺漏）。" : "这条链还没走到这一步。"}
      </p>
    </section>
  );
}

/** 五步进度条：实心✓=有记录 / ⚠=缺口 / 空心=未到达，点击跳转到对应卡片（锚点 chain-step-N）。 */
function ProgressRail({ slots, onJump }: { slots: StepSlot[]; onJump: (i: number) => void }) {
  return (
    <div className="mt-5 flex items-start">
      {slots.map((slot, i) => {
        const done = slot.state === "done";
        const gap = slot.state === "gap";
        const dotCls = done ? "border-amber bg-amber text-ink" : gap ? "border-salmon text-salmon" : "border-line text-tx3";
        const mark = done ? "✓" : gap ? "!" : "·";
        const labelCls = done ? "text-amber-hi" : gap ? "text-salmon" : "text-tx3";
        const dateText = slot.date ?? (gap ? "缺记录" : "未到达");
        const prevDone = i > 0 && slots[i - 1].state === "done";
        const nextDone = i < slots.length - 1 && slots[i + 1].state === "done";
        const lineCls =
          prevDone && nextDone
            ? "border-solid border-amber/55"
            : prevDone || nextDone
              ? "border-dashed border-salmon/60"
              : "border-solid border-line";
        return (
          <Fragment key={slot.step}>
            <button
              onClick={() => onJump(i)}
              className="flex w-[84px] flex-none cursor-pointer flex-col items-center gap-1.5 bg-transparent p-0"
            >
              <span
                className={`flex h-[26px] w-[26px] items-center justify-center rounded-full border-2 font-mono text-[12px] font-bold ${dotCls}`}
              >
                {mark}
              </span>
              <span className={`whitespace-nowrap text-[12px] ${labelCls}`}>{decisionStepAction(slot.step)}</span>
              <span className={`font-mono text-[10px] ${gap ? "text-salmon/75" : "text-tx3"}`}>{dateText}</span>
            </button>
            {i < slots.length - 1 && <span className={`mt-3 h-0 min-w-3 flex-1 border-t-2 ${lineCls}`} />}
          </Fragment>
        );
      })}
    </div>
  );
}

function SimilarSection({
  view,
  onOpen,
}: {
  view: SimilarDecisionsView | null;
  onOpen: (projectId: string) => void;
}) {
  return (
    <div className="mt-6">
      <div className="flex items-baseline justify-between border-b border-line pb-2">
        <h2 className="text-[13px] font-semibold text-cream">相似历史</h2>
        <span className="text-[10.5px] text-tx3">
          {view ? `方式：${view.mode === "semantic" ? "语义" : "结构（同仓+最近）"} · §6.5` : "§6.5"}
        </span>
      </div>
      {view === null ? (
        <LoadingLine text="相似历史加载中…" />
      ) : view.hits.length === 0 ? (
        <p className="mt-3 rounded-hard border border-line bg-panel px-4 py-6 text-center text-[11.5px] text-tx3">
          暂无相似历史（空命中是诚实数据，不是错误）
        </p>
      ) : (
        <div className="mt-3 space-y-2">
          {dedupeByProject(view.hits).map((h) => (
            <HitCard
              key={h.decision_id}
              hit={h}
              mode={view.mode === "semantic" ? "semantic" : "structural"}
              onOpen={onOpen}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ══════════════ 主页面 ══════════════

export function DecisionChainPage({
  organizationId,
  onToast,
}: {
  /** live 模式 trace/similar 的 L1 命名空间；null = 跨组织（审计人员未必知道归属组织） */
  organizationId: string | null;
  onToast: (text: string) => void;
}) {
  const [mode, setMode] = useState<SearchMode>("semantic");
  const [semanticQuery, setSemanticQuery] = useState("");
  const [idQuery, setIdQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [semanticHits, setSemanticHits] = useState<SimilarDecisionView[] | null>(null);
  const [lastQuery, setLastQuery] = useState<string | null>(null);

  const [trace, setTrace] = useState<DecisionChainView | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [similar, setSimilar] = useState<SimilarDecisionsView | null>(null);

  // 需求定位：候选列表（null = 还没搜过；[] = 搜了但没有命中）
  const [candidates, setCandidates] = useState<DecisionProjectCandidate[] | null>(null);
  const [candidateBusy, setCandidateBusy] = useState(false);
  const [candidateError, setCandidateError] = useState<string | null>(null);

  const [refreshBusy, setRefreshBusy] = useState(false);
  const [refreshNote, setRefreshNote] = useState<string | null>(null);

  const sourceMode = decisionChainSourceMode();

  /** 语义检索：命中列表可点击进入追溯（列表与链并存，方便连点几条对比）。 */
  const runSemanticSearch = useCallback(
    async (query: string) => {
      const q = query.trim();
      if (!q) return;
      setSearching(true);
      setSearchError(null);
      try {
        const view = await searchSemanticDecisions(q, { organizationId, topK: 5 });
        setSemanticHits(view.hits);
        setLastQuery(q);
      } catch (err) {
        setSearchError(errText(err));
      } finally {
        setSearching(false);
      }
    },
    [organizationId],
  );

  /** 打开某个项目的完整决策链（语义命中点击 / 需求定位提交两条路共用），
   *  并顺带拉它的相似历史（§6.5，semantic 探针用需求文本，缺 embedding 时
   *  后端回退 structural 并由 mode 如实报告）。 */
  const openTrace = useCallback(
    async (projectId: string) => {
      setTraceLoading(true);
      setTraceError(null);
      setTrace(null);
      setSimilar(null);
      try {
        const chain = await fetchDecisionChain(projectId, organizationId);
        setTrace(chain);
        const probe = chain.requirement?.text ?? undefined;
        fetchSimilarDecisions(chain.project_id, chain.organization_id, {
          mode: probe ? "semantic" : "structural",
          queryText: probe,
          topK: 5,
        })
          .then((view) => setSimilar(view))
          .catch(() => setSimilar(null)); // 相似历史失败不拖垮链本身
      } catch (err) {
        setTraceError(errText(err));
      } finally {
        setTraceLoading(false);
      }
    },
    [organizationId],
  );

  // replay 演示：进页面自动跑一次示范语义检索，让用户立刻看到效果。
  useEffect(() => {
    if (sourceMode !== "replay") return;
    const demo = "订单结账时记录价格调整原因";
    setSemanticQuery(demo);
    let cancelled = false;
    setSearching(true);
    searchSemanticDecisions(demo, { organizationId, topK: 5 })
      .then((view) => {
        if (cancelled) return;
        setSemanticHits(view.hits);
        setLastQuery(demo);
      })
      .catch((err: unknown) => {
        if (!cancelled) setSearchError(errText(err));
      })
      .finally(() => {
        if (!cancelled) setSearching(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshEmbeddings = async () => {
    setRefreshBusy(true);
    setRefreshNote(null);
    try {
      const view = await refreshDecisionEmbeddings();
      setRefreshNote(`已向量化 ${view.refreshed} 张决策单（存量批量刷新）`);
      onToast(`向量库刷新完成：${view.refreshed} 张`);
    } catch (err) {
      setRefreshNote(errText(err));
    } finally {
      setRefreshBusy(false);
    }
  };

  /** 需求定位搜索：输入可以是标题关键词 / #短id / UUID。命中唯一时直接追溯
   *  （replay 的 resolveReplayProjectId 兜底），否则返回候选列表让用户点选。 */
  const searchCandidates = useCallback(
    async (keyword: string) => {
      setCandidateBusy(true);
      setCandidateError(null);
      setCandidates(null);
      // 换词搜索时清掉旧追溯，避免下方残留上一个项目的链
      setTrace(null);
      setSimilar(null);
      setTraceError(null);
      try {
        setCandidates(await locateProjectCandidates(keyword, organizationId));
      } catch (err) {
        setCandidateError(errText(err));
      } finally {
        setCandidateBusy(false);
      }
    },
    [organizationId],
  );

  const submitById = () => {
    const raw = idQuery.trim();
    if (!raw) return;
    // 剥掉从 issue 列表复制来的 # 前缀
    const stripped = raw.replace(/^#/, "").trim();
    if (sourceMode === "replay") {
      // 演示剧本：完整 UUID 或 8 位 shortId 前缀优先直查，解析后回填完整 id。
      const resolved = resolveReplayProjectId(stripped);
      if (resolved) {
        setIdQuery(resolved);
        setCandidates(null);
        void openTrace(resolved);
        return;
      }
      void searchCandidates(stripped);
      return;
    }
    // live：先按 UUID 识别（避免 422 裸抛），否则当标题关键词搜候选。
    const parsed = parseProjectInput(stripped);
    if (parsed.kind === "id") {
      setIdQuery(parsed.id);
      setCandidates(null);
      void openTrace(parsed.id);
      return;
    }
    void searchCandidates(parsed.keyword);
  };

  const openFromHit = (projectId: string) => {
    setMode("id");
    setIdQuery(projectId);
    setCandidates(null);
    void openTrace(projectId);
  };

  // 链级汇总（v3）：终态徽章 + 五步槽位。进度条与卡片序列共用同一视图模型，两边永远一致。
  const outcome = trace && trace.nodes.length > 0 ? chainOutcome(trace.nodes) : null;
  const slots = trace ? chainStepSlots(trace) : null;
  const jumpToStep = (i: number) => {
    document.getElementById(`chain-step-${i}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="max-w-[980px]">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-line pb-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[16px] font-semibold text-cream">历史决策</h1>
          <span className="text-[11.5px] text-tx2">
            决策链追溯 · 语义检索 / 需求定位 · decision-chain-v0.1 §6
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[10.5px] text-tx3">
            {sourceMode === "live"
              ? "数据源：live · /api/v1/decision-chains（Bearer agent_action_token）"
              : "数据源：replay 夹具 · 加 ?source=live 打真实读模型"}
          </span>
          <button onClick={() => void refreshEmbeddings()} disabled={refreshBusy} className={buttonCls}>
            刷新向量库
          </button>
        </div>
      </div>
      {refreshNote && <p className="mt-2 text-[10.5px] text-tx3">{refreshNote}</p>}

      {/* 两种入口 */}
      <div className="mt-4 rounded-hard border border-line bg-panel px-4 py-4">
        <div className="flex gap-1 border-b border-line">
          {(
            [
              ["semantic", "语义检索"],
              ["id", "需求定位"],
            ] as Array<[SearchMode, string]>
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setMode(key)}
              className={`border-b-2 px-3 pb-1.5 text-[11.5px] transition-colors ${
                mode === key ? "border-amber text-cream" : "border-transparent text-tx3 hover:text-tx2"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {mode === "semantic" ? (
          <form
            className="mt-3 flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void runSemanticSearch(semanticQuery);
            }}
          >
            <input
              value={semanticQuery}
              onChange={(e) => setSemanticQuery(e.target.value)}
              placeholder="用自然语言描述要找的历史决策，如：订单结账时记录价格调整原因"
              className={`${inputCls} min-w-0 flex-1`}
            />
            <button type="submit" disabled={searching || !semanticQuery.trim()} className={buttonCls}>
              {searching ? "检索中…" : "检索"}
            </button>
          </form>
        ) : (
          <form
            className="mt-3 flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              submitById();
            }}
          >
            <input
              value={idQuery}
              onChange={(e) => setIdQuery(e.target.value)}
              placeholder={
                sourceMode === "replay"
                  ? `标题关键词 / #${shortId(MAIN_PROJECT_ID)} / 完整 UUID——输 issue 列表里看到的信息即可`
                  : "标题关键词 / #短id / 完整 UUID——不知道 id 就输标题关键词"
              }
              className={`${inputCls} min-w-0 flex-1`}
            />
            <button type="submit" disabled={traceLoading || !idQuery.trim()} className={buttonCls}>
              {traceLoading ? "追溯中…" : "追溯"}
            </button>
          </form>
        )}
        <p className="mt-2 text-[10.5px] leading-relaxed text-tx3">
          {mode === "semantic"
            ? "语义检索 = 跨组织按文本搜相似需求：每个项目贡献一条命中（其与查询最相似的决策单），点卡片展开该需求的完整决策链。embedding 未配置时该入口如实报错，不回退结构相似。"
            : "需求定位 = 输入你在 issue 列表里看到的信息（标题关键词 / #短id / 完整 UUID）找到对应项目；唯一命中直接追溯，多条则列出候选供点选。organization 未知可省略（跨组织搜索）。"}
        </p>
      </div>

      {/* 语义检索结果 */}
      {mode === "semantic" && (searchError || searching || semanticHits) && (
        <div className="mt-4">
          {searchError && (
            <p className="rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3 text-[11.5px] text-salmon">
              {searchError}
            </p>
          )}
          {searching && <LoadingLine text="语义检索中…" />}
          {!searching && !searchError && semanticHits && (
            <>
              <div className="flex items-baseline justify-between border-b border-line pb-2">
                <h2 className="text-[13px] font-semibold text-cream">相似需求命中</h2>
                <span className="text-[10.5px] text-tx3">
                  「{lastQuery}」 · 每需求一条 · 按相似度降序
                </span>
              </div>
              {semanticHits.length === 0 ? (
                <p className="mt-3 rounded-hard border border-line bg-panel px-4 py-6 text-center text-[11.5px] text-tx3">
                  没有命中——「还没有相似历史」是诚实数据，不是错误
                </p>
              ) : (
                <div className="mt-3 space-y-2">
                  {dedupeByProject(semanticHits).map((h) => (
                    <HitCard key={h.decision_id} hit={h} mode="semantic" onOpen={openFromHit} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* 需求定位候选（mode=id 且执行过定位时展示；点选才追溯，不自动开链） */}
      {mode === "id" && (candidateBusy || candidateError || candidates) && (
        <div className="mt-4">
          {candidateBusy && <LoadingLine text="定位候选项目中…" />}
          {candidateError && (
            <p className="rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3 text-[11.5px] text-salmon">
              {candidateError}
            </p>
          )}
          {!candidateBusy && !candidateError && candidates && (
            <>
              <div className="flex items-baseline justify-between border-b border-line pb-2">
                <h2 className="text-[13px] font-semibold text-cream">候选项目</h2>
                <span className="text-[10.5px] text-tx3">
                  {sourceMode === "replay"
                    ? "决策链夹具 · 按 id 前缀 / 标题关键词匹配"
                    : "基于当前加载的 issue 列表（open + closed 第一页）· 不是全量"}
                </span>
              </div>
              {candidates.length === 0 ? (
                sourceMode === "replay" ? (
                  <div className="mt-3">
                    <p className="rounded-hard border border-line bg-panel px-4 py-3 text-center text-[11.5px] text-tx3">
                      没有关键词命中「{idQuery.trim()}」——演示剧本当前可追溯这 {DECISION_PROJECT_META.length}{" "}
                      个 issue，点选直接看链：
                    </p>
                    <div className="mt-2 space-y-2">
                      {DECISION_PROJECT_META.map((p) => (
                        <button
                          key={p.id}
                          onClick={() => openFromHit(p.id)}
                          className="w-full rounded-hard border border-line bg-panel px-4 py-3 text-left transition-colors hover:border-amber-hi/50"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono text-[10.5px] text-tx3">#{shortId(p.id)}</span>
                            <span className="ml-auto text-[10.5px] text-tx3">点击追溯</span>
                          </div>
                          <p className="mt-1 text-[11.5px] leading-relaxed text-cream">{p.title}</p>
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <p className="mt-3 rounded-hard border border-line bg-panel px-4 py-6 text-center text-[11.5px] text-tx3">
                    没有匹配的项目——换个关键词，或直接粘贴 issue 列表里的 #短id
                  </p>
                )
              ) : (
                <div className="mt-3 space-y-2">
                  {candidates.map((c) => (
                    <button
                      key={c.project_id}
                      onClick={() => openFromHit(c.project_id)}
                      className="w-full rounded-hard border border-line bg-panel px-4 py-3 text-left transition-colors hover:border-amber-hi/50"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-[10.5px] text-tx3">#{shortId(c.project_id)}</span>
                        {c.note && <span className={`${chipCls} border-line text-tx2`}>{c.note}</span>}
                        {c.latest_at && (
                          <span className="ml-auto text-[10.5px] tabular-nums text-tx3">
                            最近决策 {dayLabel(c.latest_at)}
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-[11.5px] leading-relaxed text-cream">{c.title}</p>
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* 需求定位/追溯结果（语义命中点击与需求定位提交共用） */}
      {(mode === "id" || trace || traceLoading || traceError) && (
        <div className="mt-4">
          {traceLoading && <LoadingLine text="决策链追溯中…" />}
          {traceError && (
            <p className="rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3 text-[11.5px] text-salmon">
              {traceError}
            </p>
          )}
          {!traceLoading && !traceError && trace && (
            <>
              {/* 链头卡：需求原文 + 终态徽章 + 全量审计 ids（读者第一眼=这条链是关于什么的、最后怎么样了） */}
              <div className="rounded-hard border border-line bg-panel px-[22px] pb-4 pt-5">
                <span className="eyebrow">决策链 · #{shortId(trace.project_id)}</span>
                <p className="mt-2.5 text-[15.5px] leading-[1.7] text-cream">
                  {trace.requirement?.text ?? "（读模型未给需求文本——链存在但需求根缺失）"}
                </p>
                {outcome && (
                  <span
                    className={`mt-4 inline-block rounded border px-3 py-1 font-mono text-[12px] font-bold tracking-[0.08em] ${outcome.skin}`}
                  >
                    {outcome.label}
                  </span>
                )}
                <p className="mt-3.5 break-all font-mono text-[10.5px] text-tx3">
                  项目 {trace.project_id}
                  {trace.requirement?.snapshot_id && ` · 需求快照 ${trace.requirement.snapshot_id}`}
                  {trace.requirement?.plan_version != null && ` · 计划版本 v${trace.requirement.plan_version}`}
                </p>
              </div>

              {/* 五步进度条：点击跳转到对应卡片；实心✓/⚠缺口/空心未到达 */}
              {slots && trace.nodes.length > 0 && <ProgressRail slots={slots} onJump={jumpToStep} />}

              {/* 五步序列：实心卡=有记录（版本聚合），幽灵卡=缺口/未到达（如实占位） */}
              {trace.nodes.length === 0 ? (
                <p className="mt-3 rounded-hard border border-line bg-panel px-4 py-6 text-center text-[11.5px] text-tx3">
                  该项目尚无决策单（投影器可能尚未排空）——空链是诚实数据
                </p>
              ) : (
                slots?.map((slot, i) =>
                  slot.state === "done" ? (
                    <StepCard key={slot.step} slot={slot} stepIndex={i} />
                  ) : (
                    <GhostCard key={slot.step} slot={slot} stepIndex={i} />
                  ),
                )
              )}

              <SimilarSection view={similar} onOpen={openFromHit} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
