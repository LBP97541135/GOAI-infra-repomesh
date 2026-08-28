import { useCallback, useEffect, useRef, useState } from "react";
import type {
  DiscoveryAnalysisBlock,
  DiscoveryCandidateItem,
  DiscoveryCandidatesBlock,
  DiscoveryStepError,
  DiscoveryTaskView,
  DiscoveryTier,
  DiscoveryView,
} from "../api/contract";
import type { PlanAnchor } from "../types";
import { ApiError } from "../api/client";
import { AuthError } from "../api/auth";
import { fetchPolicyDraft } from "../api/humanControl";
import {
  fetchDiscovery,
  fetchDiscoveryTask,
  materializeDiscovery,
  newIdempotencyKey,
  submitDiscoveryApproval,
  triggerAnalysis,
  triggerCandidates,
  triggerClassification,
  triggerPlan,
} from "../api/discovery";
import { resolveGovernanceAgent } from "../api/decisions";
import { resolveDataSourceMode } from "../api/source";
import { agentLabel, errText, shortId } from "../display";
import { DiscoveryApproval, type ApprovalPrincipal } from "./DiscoveryApproval";
import { MaterializeModal } from "./MaterializeModal";
import { SupervisionPolicyCard, type PolicyDraftState } from "./SupervisionPolicyCard";
import { SupervisionPolicyDialog } from "./SupervisionPolicyDialog";

/** issue 详情页 · 发现面板（批次 B-1/B-2）。
 *  契约 `docs/contracts/delivery-read-model-v0.4.md`；设计定稿
 *  `docs/development/full-loop-gui-design-20260812.md` ②；版式基准
 *  `frontend-prototype/full-loop-surfaces.html` ②。
 *
 *  ── 本面最要紧的一条 ───────────────────────────────────────────────
 *  **步进器走到哪，前端一个字都不判。** 契约 §3.2 的七条按序判定唯一实现在读模型，
 *  投影成 `step`(1..4) 与 `step_state`。本文件里搜不到那七条规则的任何影子实现：
 *  步进器渲染 `view.step`，每一步的触发按钮也只按 `view.step >= N` 开闭——那个 N
 *  的比较用的是读模型给的数，不是前端从 analysis/candidates 是否为空推出来的。
 *  两份判定一旦并存，漂移的那天界面会理直气壮地指错步。
 *
 *  ── 其余几条 ──────────────────────────────────────────────────────
 *  **不留本地结果副本。**每一步的结果一律现读 `view`。Q8 裁决上游重跑**自动作废
 *  下游**（服务端把下游步块置回 null），只要前端不缓存旧块，被作废的步就自动回到
 *  未完成态、不残留旧结果；缓存一份就会同屏出现两代互相矛盾且都标着「已完成」的结果。
 *
 *  **在途任务的句柄取 `view.running_task_id`**，不另存一份本地 task_id：任务记录
 *  是进程内的（§4.5），别的标签页触发的任务、服务重启后丢失的任务，只有读投影说了算。
 *
 *  **失败一律显服务端 detail 原文**，不显假进度（§3.1 诚实条款 + 设计稿 ② 末条）。 */

const POLL_MS = 2000;

type StepKey = "analysis" | "candidates" | "classification" | "plan" | "approval" | "materialize";

const STEP_TITLES = ["需求分析", "候选评分", "分档审批", "生成计划"];

/** 物化开工（C-3）要用的、发现读投影**之外**的事实。都来自 issue 详情与计划纸面，
 *  由容器持有并传进来——发现面板自己再取一遍 issue 详情就成了第二个取数点。
 *
 *  步进器仍只有四格：物化不是发现链的第五步（发现四步改的是同一份草稿快照，
 *  物化建的是执行面的实体）。 */
export interface MaterializeContext {
  /** `detail.rounds.length`。非 0 = 已物化，按钮不再出现，改显已物化留痕。 */
  roundCount: number;
  /** M（「每仓一队」）：计划纸面 `execution_batches` 去重后的仓库数。
   *  计划纸面未就绪时为 null——弹窗照实说取不到，不拿别的数顶替。 */
  planRepositoryCount: number | null;
  /** 计划里 catalog 查无仓库的节点数，用于弹窗旁注（M 与实际建队数可能不等）。 */
  planUnresolvedCount: number;
}

/** 监管策略草稿卡片的门（迁移 5-1b · F4，设计文档 §3.4）。
 *
 *  **这一判由 issue 详情页的拓扑取数给出，本面板不自己再问一遍**：`GET
 *  /projects/{id}/topology` 已经是那一段（5-1a）的取数，物化那一瞬间它会从 404 翻成
 *  200，两个取数点必然在某个时刻给出互相矛盾的答案——一个还说「可以配」，另一个
 *  已经在显示真档案。
 *
 *   - `open`（拓扑 404）＝ 还没有档案，草稿窗口开着，卡片出现；
 *   - `sealed`（拓扑 200 或 403）＝ 档案已存在（403 的含义是「存在但你读不到」，
 *     后端 404 的判定在权限判定之前），走 5-1a 的只读显示，**卡片不再出现**——
 *     不给一个按了也没用的按钮；
 *   - `resolving` / `unknown` ＝ 还不知道。此时**不出卡片**：出了就得在「配置」按钮
 *     背后押一把「多半还没物化」，押错就是让人对着一份已锁死的档案填半天表。 */
export type PolicyGate = "resolving" | "open" | "sealed" | "unknown";

/* ── 步进器 ─────────────────────────────────────────────────────────────── */

/** 态色照设计定稿：完成 = 橄榄绿、当前 = 琥珀、未到 = 弱化。全部取既有令牌。
 *  失败态用赭红——那是本仓既有的失败语义（不是新增颜色语义），把失败画成「当前」
 *  会让人以为还在正常推进。 */
function Stepper({ step, stepState }: { step: DiscoveryView["step"]; stepState: DiscoveryView["step_state"] }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {STEP_TITLES.map((title, i) => {
        const n = i + 1;
        const current = n === step;
        // 规则 7（step 4 且 done）下第 4 格自己也算完成，故 done 时把当前格并进完成
        const finished = n < step || (current && stepState === "done");
        const failed = current && stepState === "failed";
        const running = current && stepState === "running";

        const skin = failed
          ? "border-salmon text-salmon"
          : finished
            ? "border-olive text-olive"
            : current
              ? "border-amber text-amber"
              : "border-line text-tx3";

        return (
          <span key={title} className="flex items-center gap-1.5">
            <span className={`rounded-hard border px-2 py-px font-mono text-[11px] ${skin}`}>
              {running && <i className="blink mr-1 inline-block size-[5px] rounded-full bg-amber align-middle not-italic" />}
              {n} {title}
              {finished && " ✓"}
              {failed && " 失败"}
              {running && " 进行中"}
              {current && !finished && !failed && !running && " ◀ 当前"}
            </span>
            {n < STEP_TITLES.length && <span className="text-[10px] text-tx3">→</span>}
          </span>
        );
      })}
    </div>
  );
}

/* ── 小件 ───────────────────────────────────────────────────────────────── */

/** 步块失败：服务端错误原文摘要原样显示，不显进度条（§2.2 / 设计稿 ② 末条）。 */
function StepErrorLine({ error }: { error: DiscoveryStepError }) {
  return (
    <p className="mt-1 rounded-hard border border-salmon/60 bg-salmon/10 px-2.5 py-1.5 text-[11.5px] text-salmon">
      服务端报错（{error.at}）：{error.message}
    </p>
  );
}

function Section({
  n,
  step,
  children,
}: {
  n: number;
  step: DiscoveryView["step"];
  children: React.ReactNode;
}) {
  return (
    <div className={`mt-2.5 border-t border-line pt-2.5 ${n > step ? "opacity-55" : ""}`}>
      <div className="microlabel pb-1.5">
        {n} {STEP_TITLES[n - 1]}
      </div>
      {children}
    </div>
  );
}

/** 候选行：mono 仓名 + 评分条 + 分数 + rationale 原样。
 *  rationale 默认只占一行（首行可见），点开看全文——**折叠不是摘要**：DOM 里始终是
 *  完整文本，只是视觉截断，不会有一份被前端剪短的判据流出去。 */
function CandidateRow({ item, llmUsed }: { item: DiscoveryCandidateItem; llmUsed: boolean }) {
  const [open, setOpen] = useState(false);
  const pct = Math.max(0, Math.min(1, item.score)) * 100;

  return (
    <div className="border-b border-line py-2 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-cream">{item.repository_name}</span>
        {item.is_entry_point && (
          <span className="rounded-hard border border-amber px-1.5 py-px text-[10.5px] text-amber">入口仓</span>
        )}
        {/* 低信号 = 服务端自述「除名字外没有可倚仗的信号」，分数是猜测不是判定 */}
        {item.low_signal && (
          <span className="rounded-hard border border-line px-1.5 py-px text-[10.5px] text-tx3">低信号</span>
        )}
        {/* 这里**没有**「catalog 未解析」分支：§2.2 定死本块的 repository_id 不可为 null
            （不在 catalog 的候选评分阶段就被过滤了）。§5.4 计划纸面的节点是另一回事，
            那边的 null 由 PlanDagPanel 如实留痕，两处不要互相搬结论。 */}
        {/* 回退评分的条走弱色：同一根琥珀条会把词频分数看成模型评分 */}
        <span className="h-[6px] w-[84px] flex-none overflow-hidden rounded-hard bg-line">
          <i className={`block h-full ${llmUsed ? "bg-amber" : "bg-tx2"}`} style={{ width: `${pct}%` }} />
        </span>
        <span className="w-[34px] flex-none text-right font-mono text-[11.5px] text-tx">
          {item.score.toFixed(2)}
        </span>
      </div>

      <button
        className={`mt-1 block w-full text-left text-[11.5px] leading-[1.7] text-tx2 hover:text-tx ${open ? "" : "truncate"}`}
        title={open ? "收起" : "展开完整理由"}
        onClick={() => setOpen((v) => !v)}
      >
        {item.rationale}
      </button>

      {item.matched_terms.length > 0 && (
        <p className="mt-0.5 font-mono text-[10.5px] text-tx3">命中词：{item.matched_terms.join(" · ")}</p>
      )}
    </div>
  );
}

/** Step 1 的追问区：答复表单 + 强行继续。 */
function ClarifyBlock({
  analysis,
  answers,
  disabled,
  onAnswer,
  onResubmit,
  onForce,
}: {
  analysis: DiscoveryAnalysisBlock;
  answers: Record<string, string>;
  disabled: boolean;
  onAnswer: (question: string, value: string) => void;
  onResubmit: () => void;
  onForce: () => void;
}) {
  const answered = analysis.questions.filter((q) => (answers[q] ?? "").trim() !== "").length;

  return (
    <div className="mt-1.5 rounded-hard border border-amber/50 bg-panel px-2.5 py-2">
      <p className="text-[11.5px] text-amber">
        需求判定：不充分（confidence {analysis.confidence.toFixed(2)}）· {analysis.questions.length} 条追问
      </p>
      {analysis.missing_dimensions.length > 0 && (
        <p className="mt-0.5 text-[11px] text-tx3">缺失维度：{analysis.missing_dimensions.join("、")}</p>
      )}

      {analysis.questions.map((q, i) => (
        <div key={q} className="mt-2">
          <p className="text-[11.5px] text-tx2">
            {i + 1}. {q}
          </p>
          <input
            className="mt-1 w-full rounded-hard border border-line bg-ink px-2.5 py-[5px] text-[12px] text-tx placeholder:text-tx3 focus:border-amber focus:outline-none"
            placeholder="回答（留空即不回答这一条）"
            value={answers[q] ?? ""}
            disabled={disabled}
            onChange={(e) => onAnswer(q, e.target.value)}
          />
        </div>
      ))}

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          className="rounded-hard bg-amber px-3 py-[5px] text-[12px] font-extrabold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
          disabled={disabled || answered === 0}
          onClick={onResubmit}
        >
          回答追问并重新分析
        </button>
        <button
          className="rounded-hard border border-line px-2.5 py-[4px] text-[11.5px] text-tx2 hover:border-salmon hover:text-salmon disabled:opacity-60"
          disabled={disabled}
          onClick={onForce}
        >
          忽略 {analysis.questions.length} 条追问，强行继续
        </button>
      </div>
      <p className="mt-1 text-[11px] text-tx3">
        {/* 裁决 2：可强行继续，但必须留痕。契约 §4.6 把留痕落成快照 forced_continue +
            一条 platform 审计事件（发现期没有决策记录实体）。 */}
        强行继续会在本 issue 上永久留痕「忽略 {analysis.questions.length} 条追问继续」，并写一条审计事件；
        它<b className="text-tx2">不重跑模型</b>，只是放行到候选评分。答复的拼接规则在服务端，前端不拼。
      </p>
    </div>
  );
}

/* ── 面板本体 ───────────────────────────────────────────────────────────── */

export function DiscoveryPanel({
  issueId,
  issueTitle,
  organizationId,
  onToast,
  onPlanGenerated,
  onCandidateAnchor,
  materialize,
  policyGate,
  onMaterialized,
}: {
  issueId: string;
  /** 配置弹窗的抬头「需求：…」。取 issue 详情的标题，本面板不为它再取一次数。 */
  issueTitle: string;
  /** 审批主体按 issue 所属组织派生（跨组织 leader 会被后端 403） */
  organizationId: string | null;
  onToast: (text: string) => void;
  /** Step 4 集成成功后请父级刷新计划 DAG 面板（planReload 现成） */
  onPlanGenerated: () => void;
  /** 锚点回退：把候选块里的任一仓库报给容器，供计划 DAG 面板在 issue 详情
   *  `repositories` 为空时兜底取数。**取数落定后才报**（含报 null），否则容器
   *  会把「还没问过」当成「问过、没有」，草稿 issue 一进页面就先闪一屏假的空态。 */
  onCandidateAnchor: (anchor: PlanAnchor | null) => void;
  /** C-3 物化开工所需的 issue 侧事实（见 MaterializeContext） */
  materialize: MaterializeContext;
  /** 5-1b 策略草稿卡片的门（见 PolicyGate）。由容器的拓扑取数派生。 */
  policyGate: PolicyGate;
  /** 物化成功后请父级刷新**整页**详情：轮次、房间、DAG 着色全都在这一次写里变了 */
  onMaterialized: () => void;
}) {
  const mode = resolveDataSourceMode();

  const [view, setView] = useState<DiscoveryView | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  const [task, setTask] = useState<DiscoveryTaskView | null>(null);
  const [taskLost, setTaskLost] = useState(false);
  const [pollError, setPollError] = useState<string | null>(null);

  const [principal, setPrincipal] = useState<{ agentId: string; label: string } | null>(null);
  const [principalResolving, setPrincipalResolving] = useState(true);

  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<StepKey | null>(null);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [evidenceDrift, setEvidenceDrift] = useState(false);

  /** 物化确认弹窗（C-3）。错误单列一份：它要显示在弹窗里，与面板顶部的
   *  writeError 不是同一个位置（关掉弹窗就看不见了，那才是真的静默失败）。 */
  const [materializeOpen, setMaterializeOpen] = useState(false);
  const [materializeError, setMaterializeError] = useState<string | null>(null);

  /** 监管策略草稿（5-1b · F4）。**取数放在本面板而不是容器**：卡片、配置弹窗与物化
   *  弹窗三处要的是同一份，而后两者的其余入参（`effective_tiers`、`task_dag_count`）
   *  只有本面板有——放容器就得把发现读投影再往上抬一层。 */
  const [policy, setPolicy] = useState<PolicyDraftState>({ kind: "loading" });
  const [policyReload, setPolicyReload] = useState(0);
  const [policyDialogOpen, setPolicyDialogOpen] = useState(false);

  /** 幂等键（§4.1「随表单生成」、Q9「每步一个键」）。
   *  一把键代表**一次逻辑触发**：请求失败后原样重试沿用同一把（服务端据此去重，
   *  不会重跑 LLM）；一旦 202 被受理，这一把就作废——否则「上一次跑失败了、我改了
   *  答复再来一次」会被当成重放，原样返回那份失败的旧结果。 */
  const keys = useRef<Partial<Record<StepKey, string>>>({});
  const takeKey = (step: StepKey) => (keys.current[step] ??= newIdempotencyKey(step));
  const dropKey = (step: StepKey) => {
    delete keys.current[step];
  };

  useEffect(() => {
    let cancelled = false;
    // 首次加载（view 为空）才显示 loading 骨架；后续 reload 保留旧数据静默刷新，
    // 避免内容闪烁导致滚动位置丢失。
    if (!view) setLoading(true);
    setLoadError(null);
    fetchDiscovery(issueId)
      .then((next) => {
        if (cancelled) return;
        setView(next);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setView(null);
        setLoadError(errText(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [issueId, reload]);

  // 换 issue 时清掉本地草稿与在途键：答复表单是**这个** issue 的追问的答案
  useEffect(() => {
    setView(null);
    setAnswers({});
    setWriteError(null);
    setApprovalError(null);
    setEvidenceDrift(false);
    setTaskLost(false);
    setMaterializeOpen(false);
    setMaterializeError(null);
    setPolicyDialogOpen(false);
    keys.current = {};
  }, [issueId]);

  useEffect(() => {
    let cancelled = false;
    setPrincipalResolving(true);
    resolveGovernanceAgent(organizationId)
      .then((agent) => !cancelled && setPrincipal(agent))
      .catch(() => !cancelled && setPrincipal(null))
      .finally(() => !cancelled && setPrincipalResolving(false));
    return () => {
      cancelled = true;
    };
  }, [organizationId]);

  const planVersion = view?.plan_version ?? null;
  const runningTaskId = view?.running_task_id ?? null;

  /** 草稿卡片该不该在场。三个条件缺一不可：
   *   1. 拓扑还不存在（`policyGate === "open"`，见 PolicyGate）；
   *   2. 发现链已走完（step 4 且 done）——卡片与物化按钮同级，物化按钮不在的时候
   *      它也不该在；这同时保证了配置弹窗的仓库下拉有分档结果可交集；
   *   3. 本 issue 还没有轮次——已经开过工的需求，策略早已随首次物化定死。 */
  const policyWindowOpen =
    policyGate === "open" &&
    view?.step === 4 &&
    view.step_state === "done" &&
    materialize.roundCount === 0;

  /** 草稿取数。**只在卡片真会出现时才发**：否则每打开一个 issue 详情就多一次注定
   *  用不上的会话请求。门未落定/已封时不发请求，直接落成对应的门态——物化弹窗要按
   *  这两态说不同的话（见 MaterializeModal 的策略摘要）。 */
  useEffect(() => {
    if (!policyWindowOpen) {
      setPolicy(
        policyGate === "sealed"
          ? { kind: "sealed" }
          : policyGate === "resolving"
            ? { kind: "loading" }
            : { kind: "unknown" },
      );
      return;
    }
    let cancelled = false;
    setPolicy({ kind: "loading" });
    fetchPolicyDraft(issueId)
      .then((draft) => !cancelled && setPolicy({ kind: "set", draft }))
      .catch((err: unknown) => {
        if (cancelled) return;
        const status = err instanceof AuthError ? err.status : 0;
        // 404 = 还没设过，是正常起点不是错误；401/403 各有各的下一步，不能并进 error
        // （error 那一态给的是重试按钮，而过期会话与非管理员重试多少次都是同一个结果）。
        setPolicy(
          status === 404
            ? { kind: "unset" }
            : status === 401
              ? { kind: "unauthenticated", detail: errText(err) }
              : status === 403
                ? { kind: "forbidden", detail: errText(err) }
                : { kind: "error", message: errText(err) },
        );
      });
    return () => {
      cancelled = true;
    };
  }, [issueId, policyWindowOpen, policyGate, policyReload]);

  /** 回调走 ref，**不进轮询 effect 的依赖**。父级若传来一个每次 render 都换 identity
   *  的箭头函数（内联 `() => setPlanReload(n => n + 1)` 就是），把它列进依赖会让轮询
   *  每次 render 重启一轮——表现为定时器被反复清掉重建、进度看着像卡住。 */
  const callbacks = useRef({ onToast, onPlanGenerated });
  useEffect(() => {
    callbacks.current = { onToast, onPlanGenerated };
  }, [onToast, onPlanGenerated]);

  /** 锚点回退（主脑实走发现的缺口）：Step 4 走完后，草稿 issue 的拓扑仍是空的，
   *  于是 `detail.repositories` 为空、DAG 面板恒显「尚未确定范围」——尽管计划已经
   *  在快照里了。候选块的 `repository_id` **恒为真实 catalog id**（§2.2：不在 catalog
   *  的候选在评分阶段就被过滤掉了），拿它当锚点即可，§5.4 端点对本 issue 域内的仓库
   *  本就返回 200。
   *
   *  **报给容器、不直接递给 DAG 面板**：两个面板分属两个取数容器，互相 import 会让
   *  「谁负责取哪份数据」变成一张环。回调走 ref 与轮询同一个理由（父级内联箭头函数
   *  每次 render 换 identity，列进依赖会让本 effect 空转重跑）。 */
  const anchorSink = useRef(onCandidateAnchor);
  useEffect(() => {
    anchorSink.current = onCandidateAnchor;
  }, [onCandidateAnchor]);

  const anchorId = view?.candidates?.items[0]?.repository_id ?? null;
  const anchorName = view?.candidates?.items[0]?.repository_name ?? null;
  useEffect(() => {
    // 取数未落定（含换 issue 后的重取）时一个字都不报：此刻 view 还是上一份，
    // 报出去的是**别的 issue** 的仓库，容器会拿它去请求一条注定 404 的计划纸面。
    if (loading) return;
    anchorSink.current(anchorId && anchorName ? { repositoryId: anchorId, name: anchorName } : null);
  }, [loading, anchorId, anchorName]);

  /** 轮询（§4.5）。终态即停并**重取读投影**——任务视图不投影结果，
   *  「这一步到底落没落」只有 `GET …/discovery` 说了算。 */
  useEffect(() => {
    if (!runningTaskId) {
      setTask(null);
      return;
    }
    let cancelled = false;
    let timer = 0;

    const poll = () => {
      fetchDiscoveryTask(issueId, runningTaskId)
        .then((next) => {
          if (cancelled) return;
          setTask(next);
          setPollError(null);
          if (next.status === "running") {
            timer = window.setTimeout(poll, POLL_MS);
            return;
          }
          if (next.status === "succeeded" && next.step === 4) {
            callbacks.current.onToast(`计划已生成 v${planVersion ?? "?"}`);
            callbacks.current.onPlanGenerated();
          }
          setReload((n) => n + 1);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          if (err instanceof ApiError && err.status === 404) {
            // 任务记录进程内、重启即丢。这里比扫描那边有更强的保证：结果在快照里，
            // 重取读投影就知道该步落没落，不必重跑。
            setTaskLost(true);
            setTask(null);
            setReload((n) => n + 1);
            return;
          }
          setPollError(errText(err));
          timer = window.setTimeout(poll, POLL_MS);
        });
    };

    poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // planVersion 只用于成功文案，不该成为重启轮询的理由；两个回调走 ref（见上）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issueId, runningTaskId]);

  const afterTrigger = useCallback((step: StepKey) => {
    dropKey(step);
    setTaskLost(false);
    setReload((n) => n + 1);
  }, []);

  const run = useCallback(
    (step: StepKey, call: (key: string) => Promise<unknown>) => {
      setBusy(step);
      setWriteError(null);
      call(takeKey(step))
        // 回执的 `task_id` **有意不消费**：重放（200 / status:"replayed"）时它恒为 null，
        // 而在途任务的唯一权威是读投影的 `running_task_id`。认回执就得在两种 status 上
        // 分叉出两套等待逻辑，还会漏掉「别的标签页起的任务」这一种。
        .then(() => afterTrigger(step))
        // 409（前置未满足 / 已有在跑任务）、403（主体不合格）、503（LLM 未配置）
        // 都在这里，服务器 detail 原文展示——键留着，原样重试沿用同一把
        .catch((err: unknown) => setWriteError(errText(err)))
        .finally(() => setBusy(null));
    },
    [afterTrigger],
  );

  const agentId = principal?.agentId ?? null;

  const startAnalysis = (payload: { answers?: { question: string; answer: string }[]; force?: boolean }) => {
    if (!agentId) return;
    run("analysis", (key) =>
      triggerAnalysis(issueId, {
        created_by_agent_id: agentId,
        idempotency_key: key,
        ...(payload.answers ? { answers: payload.answers } : {}),
        ...(payload.force ? { force_continue: true } : {}),
      }),
    );
  };

  const handleApproval = (
    decision: "approved" | "changes_requested",
    reason: string,
    adjustments: { repository: string; tier: DiscoveryTier }[],
  ) => {
    if (!agentId || !view) return;
    // 提交的是**当前分档的指纹**（顶层 classification_evidence_version），不是
    // approval.evidence_version——后者是「上一次决定绑在哪份证据上」，未审批时为 null、
    // 已审批时是旧指纹，两种都必然换来 409。§3.1 的两字段分工表把这条写死了。
    const evidence = view.classification_evidence_version;
    if (!evidence) {
      setApprovalError("当前分档没有证据指纹（classification_evidence_version 为空），无法提交审批。");
      return;
    }
    setBusy("approval");
    setApprovalError(null);
    setEvidenceDrift(false);
    submitDiscoveryApproval(issueId, {
      decided_by_agent_id: agentId,
      idempotency_key: takeKey("approval"),
      decision,
      reason,
      adjustments,
      evidence_version: evidence,
    })
      .then(() => {
        dropKey("approval");
        onToast(decision === "approved" ? "分档已批准，可生成计划" : "已记录「要求改动」，未放行");
        setReload((n) => n + 1);
      })
      .catch((err: unknown) => {
        // §5.3 / Q18：409 = 批的那份分档已被上游重跑覆盖，不是「重试就好」
        if (err instanceof ApiError && err.status === 409) setEvidenceDrift(true);
        setApprovalError(errText(err));
      })
      .finally(() => setBusy(null));
  };

  /** 物化开工（C-3）。幂等键**随弹窗生成**：打开取一把（已有就沿用），失败留着——
   *  原样重试是同一次逻辑物化，换新键会让服务端当成第二次、真去建第二批任务。
   *  取消即作废：重新打开是一次新的决定。 */
  const openMaterialize = () => {
    takeKey("materialize");
    setMaterializeError(null);
    setMaterializeOpen(true);
  };

  const closeMaterialize = () => {
    setMaterializeOpen(false);
    setMaterializeError(null);
    dropKey("materialize");
  };

  const handleMaterialize = () => {
    if (!agentId) return;
    setBusy("materialize");
    setMaterializeError(null);
    materializeDiscovery(issueId, { created_by_agent_id: agentId, idempotency_key: takeKey("materialize") })
      .then((result) => {
        dropKey("materialize");
        setMaterializeOpen(false);
        // `repositories[]` 的元素语义（id 还是名）未定稿，故只报数不报内容
        onToast(
          result.status === "replayed"
            ? `已物化（同幂等键重放，未重复创建）· 计划 ${shortId(result.plan_id)} · ${result.task_ids.length} 任务 · ${result.team_count} 团队`
            : `已物化并开工 · 计划 ${shortId(result.plan_id)} · ${result.task_ids.length} 任务 · ${result.team_count} 团队`,
        );
        // 轮次、房间、DAG 着色全在这一次写里变了 → 刷整页，而不是只刷本面板
        onMaterialized();
        setReload((n) => n + 1);
      })
      // 409（检查点未过 / 计划未生成…）、403、404 一律服务端 detail 原文进弹窗，
      // 不翻译不软化——归并成一句「物化失败」会把可自助解决的前置问题伪装成故障
      .catch((err: unknown) => setMaterializeError(errText(err)))
      .finally(() => setBusy(null));
  };

  /* ── 渲染 ─────────────────────────────────────────────────────────────── */

  const header = (
    <div className="microlabel flex items-baseline gap-2 pt-5 pb-2">
      发现
      <span className="text-[10px] tracking-normal text-tx3">
        需求分析 → 候选评分 → 分档审批 → 生成计划（步进器位置由读模型判定）
      </span>
    </div>
  );

  if (loading) {
    return (
      <>
        {header}
        <p className="py-3 text-[12px] text-tx2">发现链状态加载中…</p>
      </>
    );
  }

  if (loadError || !view) {
    return (
      <>
        {header}
        <p className="text-[12px] text-salmon">
          发现链读投影取用失败：{loadError ?? "未取到数据"}
          <button className="pl-2 text-tx2 underline hover:text-amber-hi" onClick={() => setReload((n) => n + 1)}>
            重试
          </button>
        </p>
      </>
    );
  }

  const { step, step_state: stepState, analysis, candidates, classification, approval, integration } = view;
  const running = stepState === "running";
  const anyBusy = busy !== null || running;
  const principalReady = principal !== null;
  const canWrite = mode === "live" && principalReady;

  /** 触发按钮的开闭只看**读模型给的 step**（外加「有任务在跑就不许再发」，§4.4
   *  同一 issue 至多一个在跑任务）。不看 analysis/candidates 是否为空——那就是在
   *  前端重写 §3.2。 */
  const canTrigger = (n: number) => step >= n && canWrite && !anyBusy;

  const triggerHint = (n: number) => {
    if (mode === "replay") return "回放模式不触发写请求（加 ?source=live）";
    if (principalResolving) return "审批/触发主体解析中…";
    if (!principalReady) return "花名册里没有本工作区的活跃 Org Leader，写请求主体无从派生";
    if (running) return "本 issue 已有在跑的任务，同时至多一个（§4.4）";
    if (step < n) return "上一步尚未完成，服务端会以 409 拒绝";
    return null;
  };

  const TriggerButton = ({ n, label, stepKey, onClick }: { n: number; label: string; stepKey: StepKey; onClick: () => void }) => (
    <div className="mt-1.5 flex flex-wrap items-center gap-2">
      <button
        className="rounded-hard bg-amber px-3 py-[5px] text-[12px] font-extrabold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
        disabled={!canTrigger(n)}
        onClick={onClick}
      >
        {busy === stepKey ? "提交中…" : label}
      </button>
      {triggerHint(n) && <span className="text-[11px] text-tx3">{triggerHint(n)}</span>}
    </div>
  );

  /** 重跑上游会作废下游（Q8）。提示只在**确实有下游可作废**时出现——没有下游时
   *  说这句话是在吓唬人。 */
  const RerunNote = ({ has }: { has: boolean }) =>
    has ? (
      <p className="mt-1 text-[11px] text-tx3">
        重跑本步会作废其下游步骤（契约 §4.4），下游结果将被清空并留审计。
      </p>
    ) : null;

  return (
    <>
      {header}

      <div className="rounded-hard border border-line bg-panel-2 px-3.5 py-3">
        <Stepper step={step} stepState={stepState} />

        {/* 在途任务：Q17 的逐候选进度。总数 total 由服务端给，前端不编 */}
        {running && task && (
          <p className="mt-2 text-[11.5px] text-amber">
            第 {task.step} 步进行中 · {task.progress.done} / {task.progress.total}
            {task.progress.label && <> · 当前：<span className="font-mono">{task.progress.label}</span></>}
          </p>
        )}
        {running && !task && !pollError && <p className="mt-2 text-[11.5px] text-tx2">正在读取任务进度…</p>}
        {pollError && (
          <p className="mt-2 text-[11px] text-salmon">进度请求失败（仍在重试）：{pollError.slice(0, 100)}</p>
        )}
        {taskLost && (
          <p className="mt-2 rounded-hard border border-line px-2.5 py-1.5 text-[11.5px] text-tx2">
            任务状态已随服务重启丢失（任务记录只活在那个进程里）。结果本身在快照里——
            上面这份读投影就是它，据此判断该步落没落，不必盲目重跑。
          </p>
        )}
        {writeError && (
          <p className="mt-2 rounded-hard border border-salmon/60 bg-salmon/10 px-2.5 py-1.5 text-[11.5px] text-salmon">
            {writeError}
          </p>
        )}

        {/* ── 1 需求分析 ───────────────────────────────────────────────── */}
        <Section n={1} step={step}>
          {analysis === null ? (
            <>
              <p className="text-[12px] text-tx3">尚未发起需求分析。需求文本取自本 issue 的草稿快照，界面不重复收一份。</p>
              <TriggerButton n={1} stepKey="analysis" label="开始需求分析" onClick={() => startAnalysis({})} />
            </>
          ) : (
            <>
              {analysis.error && <StepErrorLine error={analysis.error} />}

              {!analysis.error && analysis.sufficient && (
                <p className="text-[11.5px] text-olive">
                  需求判定：充分（confidence {analysis.confidence.toFixed(2)}）
                </p>
              )}

              {/* 已强行继续：留痕原样展示，追问表单不再出现（那一票已经投过了） */}
              {analysis.forced_continue && (
                <p className="text-[11.5px] text-amber">
                  已强行继续 · 忽略 {analysis.forced_continue.ignored_question_count} 条追问 ·{" "}
                  {agentLabel(null, analysis.forced_continue.by_agent_id)} · {analysis.forced_continue.at}
                </p>
              )}

              {!analysis.error && !analysis.sufficient && !analysis.forced_continue && (
                <ClarifyBlock
                  analysis={analysis}
                  answers={answers}
                  disabled={!canWrite || anyBusy}
                  onAnswer={(q, v) => {
                    setAnswers((prev) => ({ ...prev, [q]: v }));
                    // 改了答复就是一次**新的**逻辑请求，旧键作废（否则会被当重放）
                    dropKey("analysis");
                  }}
                  onResubmit={() =>
                    startAnalysis({
                      answers: analysis.questions
                        .filter((q) => (answers[q] ?? "").trim() !== "")
                        .map((q) => ({ question: q, answer: answers[q].trim() })),
                    })
                  }
                  onForce={() => startAnalysis({ force: true })}
                />
              )}

              {analysis.answers.length > 0 && (
                <div className="mt-1.5">
                  <div className="microlabel pb-1">已提交的答复</div>
                  {analysis.answers.map((a, i) => (
                    <p key={`${a.question}-${i}`} className="text-[11.5px] text-tx2">
                      {a.question} — <span className="text-tx">{a.answer}</span>
                    </p>
                  ))}
                </div>
              )}

              {analysis.extracted_keywords.length > 0 && (
                <p className="mt-1 font-mono text-[10.5px] text-tx3">
                  关键词：{analysis.extracted_keywords.join(" · ")}
                </p>
              )}

              {analysis.error && (
                <TriggerButton n={1} stepKey="analysis" label="重试需求分析" onClick={() => startAnalysis({})} />
              )}
              <RerunNote has={candidates !== null || classification !== null} />
            </>
          )}
        </Section>

        {/* ── 2 候选评分 ───────────────────────────────────────────────── */}
        <Section n={2} step={step}>
          {candidates === null ? (
            <>
              <p className="text-[12px] text-tx3">尚未评分。发现将按需求文本在 catalog 里给候选仓库打分。</p>
              <TriggerButton
                n={2}
                stepKey="candidates"
                label="开始候选评分"
                onClick={() =>
                  agentId &&
                  run("candidates", (key) =>
                    triggerCandidates(issueId, { created_by_agent_id: agentId, idempotency_key: key }),
                  )
                }
              />
            </>
          ) : (
            <CandidatesBlock block={candidates} />
          )}
          {candidates !== null && (
            <>
              {candidates.error && <StepErrorLine error={candidates.error} />}
              <TriggerButton
                n={2}
                stepKey="candidates"
                label={candidates.error ? "重试候选评分" : "重新评分"}
                onClick={() =>
                  agentId &&
                  run("candidates", (key) =>
                    triggerCandidates(issueId, { created_by_agent_id: agentId, idempotency_key: key }),
                  )
                }
              />
              <RerunNote has={classification !== null} />
            </>
          )}
        </Section>

        {/* ── 3 分档审批 ───────────────────────────────────────────────── */}
        <Section n={3} step={step}>
          {classification === null ? (
            <>
              <p className="text-[12px] text-tx3">
                尚未生成三档分类。分类由服务端从上一步的候选块取输入（浏览器不回传候选与证据）。
              </p>
              <TriggerButton
                n={3}
                stepKey="classification"
                label="生成三档分类"
                onClick={() =>
                  agentId &&
                  run("classification", (key) =>
                    triggerClassification(issueId, { created_by_agent_id: agentId, idempotency_key: key }),
                  )
                }
              />
            </>
          ) : (
            <>
              {classification.error && <StepErrorLine error={classification.error} />}
              {/* key 绑**当前分档指纹**：上游重跑换指纹即重挂，下拉草稿随之清空。
                  绑 approval.evidence_version 不行——它未审批时恒 null，四种不同的分档
                  会共用同一个 key，草稿就跨着证据活下来了（见 DiscoveryApproval 注释）。 */}
              <DiscoveryApproval
                key={view.classification_evidence_version ?? "no-evidence"}
                classification={classification}
                effectiveTiers={view.effective_tiers}
                approval={approval}
                evidenceVersion={view.classification_evidence_version}
                principal={approvalPrincipal(mode, principalResolving, principal)}
                submitting={busy === "approval"}
                errorText={approvalError}
                evidenceDrift={evidenceDrift}
                onSubmit={handleApproval}
                onReload={() => setReload((n) => n + 1)}
              />
              <TriggerButton
                n={3}
                stepKey="classification"
                label={classification.error ? "重试三档分类" : "重新分类"}
                onClick={() =>
                  agentId &&
                  run("classification", (key) =>
                    triggerClassification(issueId, { created_by_agent_id: agentId, idempotency_key: key }),
                  )
                }
              />
              <RerunNote has={approval.state !== "not_requested"} />
            </>
          )}
        </Section>

        {/* ── 4 生成计划 ───────────────────────────────────────────────── */}
        <Section n={4} step={step}>
          {integration === null ? (
            <p className="text-[12px] text-tx3">
              尚未生成计划。集成送进去的是<b className="text-tx2">生效分档</b>（含审批人的调整），不是模型原判。
            </p>
          ) : (
            <p className="text-[11.5px] text-olive">
              计划已生成 v{view.plan_version} · {integration.task_dag_count} 个任务节点 ·{" "}
              {integration.batch_count} 个执行批次 · {integration.contract_count} 份接口契约
              <span className="block text-tx3">图形化的这张计划在下方「计划 DAG」区块。</span>
            </p>
          )}
          <TriggerButton
            n={4}
            stepKey="plan"
            label={integration === null ? "生成计划" : "重新生成计划"}
            onClick={() =>
              agentId &&
              run("plan", (key) =>
                triggerPlan(issueId, { created_by_agent_id: agentId, idempotency_key: key }),
              )
            }
          />
          {approval.state !== "approved" && (
            <p className="mt-1 text-[11px] text-tx3">
              审批 v1 必经：分档未批准时本步会被服务端以 409 拒绝。
            </p>
          )}

          {/* ── 监管策略（5-1b · F4）───────────────────────────────────────
              物化区**上方**、与物化按钮同级，**不进步进器**：下面那条注释的判据
              （发现四步改的是同一份草稿快照，物化建的是执行面的实体）同样适用——
              配策略改的也是草稿、也不建实体，但它不属于「发现」这件事。发现回答
              「要动哪些仓库」，策略回答「谁来盯着」，共用一个步进器就要让「走到
              第几步」这个唯一事实源为一件与发现无关的事让路。 */}
          {policyWindowOpen && (
            <SupervisionPolicyCard
              state={policy}
              onConfigure={() => setPolicyDialogOpen(true)}
              onRetry={() => setPolicyReload((n) => n + 1)}
            />
          )}

          {/* ── 物化并开工（C-3）───────────────────────────────────────────
              出现条件只看两条**事实**：读模型说整链走完（step 4 且 done），
              且 issue 详情说还没有轮次。不看 integration 是否为空去反推——
              那就是在前端重写 §3.2 的第 7 条。

              三岔口的**次序**是有讲究的（缺陷 B-12）：先问收据，再问轮次。
              物化自 7659c89 起可重入，半截跑砸的一轮再调一次就能补完；但那种一轮
              往往**已经有轮次行**，只看 `roundCount > 0` 就会把它判成「已成交」，
              于是按钮永远消失、卡住的一轮再没有 GUI 出口——正是本缺陷。
              `materialization.status` 是服务端对这件事的原话，所以它先说话。

              收据缺席时（收据机制之前的旧轮次，如种子数据）**一律走原逻辑**：不知道
              就不猜，不拿「没有收据」反推「多半没事」。 */}
          {step === 4 && stepState === "done" && (
            <div className="mt-3 border-t border-line pt-3">
              {view.materialization?.status === "failed" ? (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      className="rounded-hard bg-amber px-4 py-2 text-[12.5px] font-extrabold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
                      disabled={anyBusy}
                      onClick={openMaterialize}
                    >
                      重试物化
                    </button>
                    {triggerHint(4) && <span className="text-[11px] text-tx3">{triggerHint(4)}</span>}
                  </div>
                  {/* 服务端原文 + 原时间戳，不改写不归类（§3.1 诚实条款）。
                      读模型只给 `status`，「卡在哪、要不要重试」由人看原文决定。 */}
                  <p className="mt-1.5 text-[11px] leading-[1.7] text-salmon">
                    上次物化失败（{view.materialization.at}）：{view.materialization.error ?? "服务端未记录原因"}
                  </p>
                  <p className="mt-1 text-[11px] leading-[1.7] text-tx3">
                    重试会<b className="text-tx2">补完这一轮</b>而不是另起一轮：服务端按 §8.3 认领上次留下的
                    痕迹，已经建好的队与任务不会重复创建。仍要创建什么，确认弹窗里数给你看。
                  </p>
                </>
              ) : materialize.roundCount > 0 ? (
                <p className="text-[11.5px] text-olive">
                  本 issue 已物化 · 第 {materialize.roundCount} 轮交付已建立。任务与团队见下方「关联仓库 · 团队」
                  与「房间」区块，执行进度在「计划 DAG」上按读模型着色。
                </p>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      className="rounded-hard bg-amber px-4 py-2 text-[12.5px] font-extrabold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
                      disabled={anyBusy}
                      onClick={openMaterialize}
                    >
                      物化并开工
                    </button>
                    {triggerHint(4) && <span className="text-[11px] text-tx3">{triggerHint(4)}</span>}
                  </div>
                  <p className="mt-1.5 text-[11px] leading-[1.7] text-tx3">
                    把计划变成任务与团队（每仓一队 + teamRoom/leaderDM 双房间）。
                    这是整条链的<b className="text-tx2">第二个不可逆动作</b>，具体要创建多少东西在确认弹窗里数给你看。
                  </p>
                </>
              )}
            </div>
          )}
        </Section>

        {/* 页脚数据源标注，照 PlanDagPanel 的惯例 */}
        <div className="mt-2.5 space-y-1 border-t border-line pt-2 font-mono text-[10px] leading-[1.7] text-tx3">
          <div>
            读投影 GET /issues/{"{id}"}/discovery（契约 v0.4 §3.1）· 草稿快照 v{view.plan_version}（发现四步与审批不涨版）·{" "}
            {mode === "replay" ? "回放夹具" : "live"}
          </div>
          <div>
            步进器位置（step={step} · {stepState}）由读模型按 §3.2 七条规则判定，本面只渲染不自判；
            上游重跑会自动作废下游（§4.4），被作废的步在这里回到未完成态、不残留旧结果。
          </div>
          <div>
            物化开工（把计划变成任务与团队）挂在第 4 步之后，但<b>不是</b>发现链的第五步——
            发现四步改的都是同一份草稿快照，物化建的是执行面的实体，所以步进器仍只有这四格。
          </div>
        </div>
      </div>

      <MaterializeModal
        open={materializeOpen}
        planVersion={view.plan_version}
        // N 取服务端计数。integration 为 null 时不该走到这（step 4 且 done 意味着
        // 集成已落），兜底显 0 而不是编一个数
        taskCount={integration?.task_dag_count ?? 0}
        teamCount={materialize.planRepositoryCount}
        unresolvedCount={materialize.planUnresolvedCount}
        policy={policy}
        principal={approvalPrincipal(mode, principalResolving, principal)}
        submitting={busy === "materialize"}
        errorText={materializeError}
        onCancel={closeMaterialize}
        onConfirm={handleMaterialize}
      />

      {/* 配置弹窗（5-1b · F3）。入口**只有一个**——上面那张卡片；详情页的只读段
          刻意不再放第二个按钮（「同一件事两个入口」是这批迁移一直在还的债）。
          `taskCount` 取集成计数，计划还没生成时为 null——弹窗的代价预告据此如实说
          「每个任务各 1 次」，不拿 0 冒充。 */}
      <SupervisionPolicyDialog
        open={policyDialogOpen}
        projectId={issueId}
        issueTitle={issueTitle}
        effectiveTiers={view.effective_tiers}
        taskCount={integration?.task_dag_count ?? null}
        onClose={() => setPolicyDialogOpen(false)}
        onSaved={(draft) => setPolicy(draft ? { kind: "set", draft } : { kind: "unset" })}
      />
    </>
  );
}

/** 候选块。`llm_used === false` 时必须显式自述关键词回退（Q11）——两条路径的
 *  产出形状完全相同，不说出来就是把词频分数当模型评分展示。 */
function CandidatesBlock({ block }: { block: DiscoveryCandidatesBlock }) {
  return (
    <>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-[11px] text-tx2">
          {block.items.length} 个候选 · 上限 {block.limit}
          {block.entry_point && ` · 入口 ${block.entry_point}`}
        </span>
        {block.llm_used ? (
          <span className="rounded-hard border border-line px-1.5 py-px text-[10.5px] text-tx3">LLM 评分</span>
        ) : (
          <span className="rounded-hard border border-salmon px-1.5 py-px text-[10.5px] text-salmon">
            关键词回退评分（LLM 未配置）
          </span>
        )}
      </div>

      {!block.llm_used && (
        <p className="mt-1 text-[11px] text-salmon">
          下面的分数由<b>关键词词频</b>算出，不是模型评的判断。两条路径的产出形状完全相同，
          所以这一行不是推测而是服务端的自述（llm_used=false）；据此审批时请自行核对判据。
        </p>
      )}

      {block.items.length === 0 ? (
        <p className="mt-1 text-[12px] text-tx3">
          本次评分没有产出任何候选仓库（catalog 里没有与需求匹配的仓库，或 catalog 为空）。
        </p>
      ) : (
        <div className="mt-1.5">
          {block.items.map((item) => (
            <CandidateRow key={item.repository_id} item={item} llmUsed={block.llm_used} />
          ))}
        </div>
      )}
    </>
  );
}

/** 审批主体展示态，与 ApprovalModal 的同款四态措辞。 */
function approvalPrincipal(
  mode: "live" | "replay",
  resolving: boolean,
  principal: { label: string } | null,
): ApprovalPrincipal {
  if (mode === "replay") return { state: "replay", label: "回放演示（不写后端）" };
  if (resolving) return { state: "resolving", label: "解析中…" };
  return principal
    ? { state: "ready", label: `AGENT ${principal.label}` }
    : { state: "missing", label: "审批主体未接入" };
}
