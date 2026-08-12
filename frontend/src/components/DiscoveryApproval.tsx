import { useState } from "react";
import type {
  ConfirmationResultView,
  DiscoveryApprovalBlock,
  DiscoveryClassificationBlock,
  DiscoveryEffectiveTier,
  DiscoveryTier,
} from "../api/contract";
import { TIER_LABEL, TIER_SKIN, agentLabel, shortId, tierStatusLabel } from "../display";

/** 分档审批（批次 B-2 / 契约 v0.4 §5.2）。设计定稿 ②：**行内下拉调整，不做拖拽分栏**。
 *
 *  三条立意：
 *
 *  **生效分档只认 `effective_tiers`**。契约 §3.1 明写它是唯一来源、前端禁止自己把
 *  `adjustments` 叠到 `classification` 上。所以行的来源是 effective_tiers，
 *  classification 三档只用来取该仓的 reason / confidence 明细。
 *
 *  **改档与放行一次提交**（§5.2）：拆成两个写会造出「改了但没批」的中间态。
 *
 *  **草稿绑在证据上**。本组件由父级以 `key={classification_evidence_version}` 挂载：
 *  上游重跑会换掉当前分档指纹（§5.3），组件随之重挂、下拉草稿清空。若草稿跨证据版本
 *  存活，用户就会拿着对 A 版分档的判断去批 B 版分档——而那正是 §5.3 的 409 要防的事，
 *  前端不该先制造出这个状态再等服务端拒绝。
 *
 *  **两个指纹分工不同，别混用**（§3.1 的表）：
 *   - `evidenceVersion`（顶层 `classification_evidence_version`）= 服务端当前分档的指纹，
 *     **提交审批回填的是它**，分档存在即非空；
 *   - `approval.evidence_version` = 已记录的那次决定绑的指纹，**审计用**，未审批时为 null。 */

const TIERS: DiscoveryTier[] = ["required", "maybe", "excluded"];

/** `sha256:<64 hex>` → 前 8 位。剥前缀再截，否则截出来的是 "sha256:9"，
 *  两份不同的证据看起来会一模一样。 */
const shortSha = (v: string) => shortId(v.replace(/^sha256:/, ""));

/** 审批主体展示态，与 ApprovalModal 的同款四态（决策主体从花名册派生，见
 *  api/decisions.ts 的单点实现——本面不新增取数路径）。 */
export type ApprovalPrincipal =
  | { state: "replay"; label: string }
  | { state: "resolving"; label: string }
  | { state: "ready"; label: string }
  | { state: "missing"; label: string };

function TierRow({
  tier,
  detail,
  draft,
  disabled,
  onChange,
}: {
  tier: DiscoveryEffectiveTier;
  detail: ConfirmationResultView | null;
  draft: DiscoveryTier;
  disabled: boolean;
  onChange: (next: DiscoveryTier) => void;
}) {
  const [open, setOpen] = useState(false);
  const dirty = draft !== tier.tier;

  return (
    <div className="border-b border-line py-2 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-cream">{tier.repository}</span>

        {/* 服务端已生效的档：下拉是**待提交的草稿**，两者不同时必须能同时看见，
            否则用户分不清「已经是这样」与「我刚改成这样」 */}
        <span className={`rounded-hard border px-1.5 py-px text-[10.5px] ${TIER_SKIN[tier.tier]}`}>
          {TIER_LABEL[tier.tier]}
        </span>
        {tier.adjusted && tier.original_tier && (
          <span className="text-[10.5px] text-tx3">
            已由审批人调整 · 模型原判 {TIER_LABEL[tier.original_tier]}
          </span>
        )}

        <select
          className="rounded-hard border border-line bg-ink px-1.5 py-[3px] font-mono text-[11.5px] text-tx focus:border-amber focus:outline-none disabled:opacity-60"
          value={draft}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value as DiscoveryTier)}
        >
          {TIERS.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        {dirty && <span className="text-[10.5px] text-amber">待提交：{TIER_LABEL[draft]}</span>}
      </div>

      {detail ? (
        <div className="mt-1 flex items-baseline gap-2">
          <span className="flex-none font-mono text-[10.5px] text-tx3">
            置信 {detail.confidence.toFixed(2)}
          </span>
          {/* 判据原样展示（诚实数据原则的审批场景延伸）：默认一行，点开看全文，不摘要 */}
          <button
            className={`min-w-0 flex-1 text-left text-[11.5px] leading-[1.7] text-tx2 hover:text-tx ${
              open ? "" : "truncate"
            }`}
            title={open ? "收起" : "展开完整判据"}
            onClick={() => setOpen((v) => !v)}
          >
            {detail.reason}
          </button>
        </div>
      ) : (
        // effective_tiers 里有、三档列表里没有：服务端两处不一致，说出来而不是留白
        <p className="mt-1 text-[11px] text-tx3">该仓在三档明细里没有对应条目，判据无从展示。</p>
      )}

      {detail && detail.missing_dependencies.length > 0 && (
        <p className="mt-1 text-[11px] text-salmon">
          缺失依赖：{detail.missing_dependencies.join("、")}
        </p>
      )}
    </div>
  );
}

export function DiscoveryApproval({
  classification,
  effectiveTiers,
  approval,
  evidenceVersion,
  principal,
  submitting,
  errorText,
  evidenceDrift,
  onSubmit,
  onReload,
}: {
  classification: DiscoveryClassificationBlock;
  effectiveTiers: DiscoveryEffectiveTier[];
  approval: DiscoveryApprovalBlock;
  /** 顶层 `classification_evidence_version`：提交审批回填的那一个（§3.1） */
  evidenceVersion: string | null;
  principal: ApprovalPrincipal;
  submitting: boolean;
  /** 提交失败的服务端 detail 原文（409 证据漂移也走这里） */
  errorText: string | null;
  evidenceDrift: boolean;
  onSubmit: (decision: "approved" | "changes_requested", reason: string, adjustments: { repository: string; tier: DiscoveryTier }[]) => void;
  onReload: () => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, DiscoveryTier>>({});
  const [reason, setReason] = useState("");

  const byRepository = new Map<string, ConfirmationResultView>();
  for (const item of [...classification.required, ...classification.maybe, ...classification.excluded]) {
    byRepository.set(item.repository, item);
  }

  const draftOf = (t: DiscoveryEffectiveTier) => drafts[t.repository] ?? t.tier;
  // 只送**真改过**的档：把没动过的行也当调整送上去，会在审计里留下一串没发生过的改档
  const adjustments = effectiveTiers
    .filter((t) => draftOf(t) !== t.tier)
    .map((t) => ({ repository: t.repository, tier: draftOf(t) }));

  // 没有当前指纹就没法提交（§5.3 要求审批绑在它看到的那份证据上），禁用而不是发一个注定 409 的请求
  const canSubmit = principal.state === "ready" && !submitting && evidenceVersion !== null;

  return (
    <div className="mt-2 rounded-hard border border-line bg-panel px-3 py-2.5">
      {effectiveTiers.length === 0 ? (
        // 分档跑过但生效档为空：这是真实形态之一（三档全空），说出来
        <p className="text-[12px] text-tx3">
          本次分档没有产出任何生效档位（effective_tiers 为空），无可审批的范围。
        </p>
      ) : (
        <div className="border-b border-line pb-1">
          {effectiveTiers.map((t) => (
            <TierRow
              key={t.repository}
              tier={t}
              detail={byRepository.get(t.repository) ?? null}
              draft={draftOf(t)}
              disabled={submitting}
              onChange={(next) => setDrafts((prev) => ({ ...prev, [t.repository]: next }))}
            />
          ))}
        </div>
      )}

      {classification.supplemented_repos.length > 0 && (
        <p className="mt-2 text-[11px] text-tx3">
          模型补充进来的仓库（不在候选评分里）：
          <span className="font-mono text-tx2"> {classification.supplemented_repos.join("、")}</span>
        </p>
      )}

      {/* 改档留痕与 LLM 原判并存（§2.2）：抹掉这段就看不出「模型说什么、人改成什么」 */}
      {classification.adjustments.length > 0 && (
        <div className="mt-2 border-t border-line pt-2">
          <div className="microlabel pb-1">改档留痕</div>
          {classification.adjustments.map((a, i) => (
            <p key={`${a.repository}-${a.at}-${i}`} className="text-[11px] text-tx2">
              <span className="font-mono text-tx">{a.repository}</span>{" "}
              {tierStatusLabel(a.from)} → {tierStatusLabel(a.to)} · {agentLabel(null, a.by_agent_id)} · {a.at}
            </p>
          ))}
        </div>
      )}

      {/* 已决状态：approved 与 changes_requested 都不隐藏后续操作——前者可因上游重跑
          而需要重批，后者本就等着改完再批 */}
      {approval.state !== "not_requested" && (
        <div
          className={`mt-2 rounded-hard border px-2.5 py-1.5 text-[11.5px] ${
            approval.state === "approved" ? "border-olive text-olive" : "border-salmon text-salmon"
          }`}
        >
          {approval.state === "approved" ? "已批准放行" : "已要求改动（未放行）"} ·{" "}
          {approval.decided_by_agent_id ? agentLabel(null, approval.decided_by_agent_id) : "决策主体未记录"} ·{" "}
          {approval.decided_at ?? "时间未记录"}
          {approval.reason && <span className="block text-tx2">意见：{approval.reason}</span>}
        </div>
      )}

      <textarea
        className="mt-2 h-[52px] w-full resize-none rounded-hard border border-line bg-ink px-2.5 py-1.5 text-[12px] text-tx placeholder:text-tx3 focus:border-amber focus:outline-none"
        placeholder="审批意见（随决策一并记录）"
        value={reason}
        disabled={submitting}
        onChange={(e) => setReason(e.target.value)}
      />

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-tx3">
          审批主体：{principal.label}
          {adjustments.length > 0 && ` · 本次改档 ${adjustments.length} 项`}
        </span>
        <span className="flex-1" />
        <button
          className="rounded-hard border border-line px-2.5 py-[4px] text-[11.5px] text-tx2 hover:border-salmon hover:text-salmon disabled:opacity-60"
          disabled={!canSubmit}
          onClick={() => onSubmit("changes_requested", reason, adjustments)}
        >
          要求改动
        </button>
        <button
          className="rounded-hard bg-amber px-3.5 py-[5px] text-[12px] font-extrabold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
          disabled={!canSubmit}
          onClick={() => onSubmit("approved", reason, adjustments)}
        >
          {submitting ? "提交中…" : "批准分档"}
        </button>
      </div>

      {principal.state === "missing" && (
        <p className="mt-1.5 text-[11px] text-tx3">
          花名册里没有本工作区的活跃 Organization Leader，审批主体无从派生——发一个查无此人的
          decided_by_agent_id 只会换来 403，故提交禁用。
        </p>
      )}
      {principal.state === "replay" && (
        <p className="mt-1.5 text-[11px] text-tx3">回放模式不写后端；加 ?source=live 后可真实审批。</p>
      )}

      {errorText && (
        <p className="mt-2 rounded-hard border border-salmon/60 bg-salmon/10 px-2.5 py-1.5 text-[11.5px] text-salmon">
          {errorText}
        </p>
      )}
      {/* §5.3 / Q18：批准必须绑在它实际看到的那份分档上。409 不是「重试就好」，
          是「你看的那份已经被重跑覆盖了」——只能重新加载再看一遍再批。 */}
      {evidenceDrift && (
        <p className="mt-1.5 text-[11.5px] text-amber">
          分档证据已漂移：服务端当前的三档结果已不是你正在看的这一份（多半是上游重跑过）。
          请重新加载后重新过目再批。
          <button className="pl-2 underline hover:text-amber-hi" onClick={onReload}>
            重新加载
          </button>
        </p>
      )}

      {/* 两个指纹都显示出来：一个是「我正在批的这份」，一个是「上次批的那份」。
          只显示一个，就没人能看出两者已经不同（也就是 409 的成因）。 */}
      <p className="mt-1.5 font-mono text-[10px] text-tx3">
        当前分档指纹 {evidenceVersion ? shortSha(evidenceVersion) : "无（分档未生成）"}
        {approval.evidence_version && ` · 上次审批绑定 ${shortSha(approval.evidence_version)}`} ·
        改档与放行一次提交（§5.2）· 幂等键随表单生成
      </p>
    </div>
  );
}
