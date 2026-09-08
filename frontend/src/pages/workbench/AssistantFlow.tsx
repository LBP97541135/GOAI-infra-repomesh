import { useState } from "react";
import type { ReactNode } from "react";
import { Check, ChevronDown, Loader2, Sparkles, X } from "lucide-react";
import type {
  DiscoveryClassificationBlock,
  DiscoveryView,
  ExternalMembersNotReadyDetail,
  IssueDetailView,
} from "../../api/contract";
import {
  externalMembersNotReady,
  materializeDiscovery,
  newIdempotencyKey,
  submitDiscoveryApproval,
} from "../../api/discovery";
import { resolveDataSourceMode } from "../../api/source";
import type { GovernanceAgent } from "../../api/decisions";
import { ShiningText } from "../../components/ui/shining-text";
import { READINESS_LABEL, READINESS_SKIN, errText, shortId } from "../../display";
import { autoTrigger } from "./autoTrigger";

/** 处理员（期 2.5 重构定稿）：发现→计划全过程的**对话式**呈现。
 *
 *  用户发完需求，处理员像模型一样直接开工：每一步是一条消息（跑着的用流光字，
 *  完成的折叠成一行结论+关键信息），没有卡片框、没有表单大块。需要人的地方
 *  （补答追问、分档审批、物化确认）就地成为**带按钮的消息**。
 *
 *  编排仍由 WorkbenchPage 的驱动器负责（哪步 idle 就触发哪一步）；本组件只负责
 *  把读投影讲成对话 + 承接人审节点的写回路。 */

export function AssistantFlow({
  detail,
  discovery,
  principal,
  clarifySending,
  repoHosts,
  onAdvanced,
  onRetryStep,
  onToast,
  planBatches,
}: {
  detail: IssueDetailView;
  /** 发现读投影（外层 2.5s 轮询）；null = 还没取到 */
  discovery: DiscoveryView | null;
  principal: GovernanceAgent | null;
  /** 追问回答发送中：输入框与按钮置灰的依据（状态在外层） */
  clarifySending: boolean;
  /** 仓库名 → 地址 host（审批可见性：占位域名在门上一眼可见） */
  repoHosts: Record<string, string>;
  /** 任何写成功后让外层整轮刷新 */
  onAdvanced: () => void;
  /** 失败重试：换新幂等键**直接**重发该步（驱动器只在 idle 开火，failed 态轮不到它） */
  onRetryStep: (step: 1 | 2 | 3 | 4) => void;
  onToast: (text: string) => void;
  /** 计划批次概览（仓库名按批次分组）——与顶部 DAG 胶囊同源（planState）；null = 尚未取到 */
  planBatches: string[][] | null;
}) {
  const replay = resolveDataSourceMode() === "replay";

  // ── 分档审批（门 1）──
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [adjustText, setAdjustText] = useState("");
  const [approving, setApproving] = useState(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const handleApproval = (decision: "approved" | "changes_requested") => {
    if (!discovery || !principal) return;
    if (replay) {
      onToast("回放模式不写后端：分档审批在 ?source=live 下真实提交。");
      return;
    }
    if (discovery.classification_evidence_version === null) {
      setApprovalError("分档证据指纹缺失，无法提交（§5.3 审批必须绑定它看到的那份证据）。");
      return;
    }
    setApproving(true);
    setApprovalError(null);
    submitDiscoveryApproval(detail.issue_id, {
      decided_by_agent_id: principal.agentId,
      idempotency_key: newIdempotencyKey("approval"),
      decision,
      reason: decision === "changes_requested" ? adjustText.trim() || "要求调整分档" : "",
      adjustments: [],
      evidence_version: discovery.classification_evidence_version,
    })
      .then(() => {
        setAdjustOpen(false);
        setAdjustText("");
        onToast(decision === "approved" ? "分档已批准，处理员继续生成计划" : "已要求调整，处理员会重新分档");
        autoTrigger.delete(`${detail.issue_id}:3`); // 重新分档是一次新触发
        onAdvanced();
      })
      .catch((err: unknown) => setApprovalError(errText(err)))
      .finally(() => setApproving(false));
  };

  // ── 物化开工（门 2）：无弹窗。按钮就地生效，单击即发；不可逆这件事由按钮
  //  title 与出错行里的服务端原话承担。成员没起来时服务端 409 的结构化成员清单
  //  就地逐行摊开——那是唯一一份「每个字段都是解法的一部分」的拒绝。 ──
  const [mBusy, setMBusy] = useState(false);
  const [mError, setMError] = useState<string | null>(null);
  const [mNotReady, setMNotReady] = useState<ExternalMembersNotReadyDetail | null>(null);
  const handleMaterialize = () => {
    if (mBusy) return;
    if (replay) {
      onToast("回放模式不写后端：物化会真实建团队、开房间。加 ?source=live 后可真实执行。");
      return;
    }
    if (!principal) return;
    setMBusy(true);
    setMError(null);
    setMNotReady(null);
    materializeDiscovery(detail.issue_id, {
      created_by_agent_id: principal.agentId,
      idempotency_key: newIdempotencyKey("materialize"),
    })
      .then(() => {
        onToast("已物化开工：团队与房间组建中，轮次会流进对话");
        onAdvanced();
      })
      .catch((err: unknown) => {
        setMNotReady(externalMembersNotReady(err));
        setMError(errText(err));
      })
      .finally(() => setMBusy(false));
  };

  return (
    <div className="flex gap-2">
      {/* ZCode 对话式：一个小图标，右边就是信息——没有身份、没有名字、没有头像框 */}
      <span className="flex-none pt-[3px] text-[11px] leading-none text-amber"><Sparkles size={12} strokeWidth={1.5} /></span>
      <div className="min-w-0 flex-1 grid gap-1">
        {!discovery && <RunLine title="正在接手需求" />}

        {discovery && (
          <>
            {/* ── 步 1 · 需求分析 ── */}
            {discovery.step === 1 && discovery.step_state !== "done" && !clarifyPendingOf(discovery) && (
              <RunLine title="正在分析需求" />
            )}
            {clarifyPendingOf(discovery) && (
              <div className="text-[12px] leading-[1.75] text-tx">
                <p>需求分析发现这些信息还不清楚：</p>
                <ol className="mt-1 list-decimal pl-5 text-tx2">
                  {discovery.analysis?.questions.map((q) => (
                    <li key={q}>{q}</li>
                  ))}
                </ol>
                <p className="mt-1 text-tx2">
                  {clarifySending ? <ShiningText text="正在带着你的补充继续分析…" className="text-[12px]" /> : "在下方输入框直接回答，发送后我继续分析。"}
                </p>
              </div>
            )}
            {(discovery.step > 1 || (discovery.step === 1 && discovery.step_state === "done" && !clarifyPendingOf(discovery))) &&
              discovery.analysis !== null && (
              <ExpandDone
                title="需求已解析"
                summary={
                  discovery.analysis.extracted_keywords.length > 0
                    ? `关键词：${discovery.analysis.extracted_keywords.slice(0, 6).join("・")}`
                    : null
                }
              >
                <KeywordDetail analysis={discovery.analysis} />
              </ExpandDone>
            )}
            {discovery.step === 1 && discovery.step_state === "failed" && (
              <FailLine
                title="需求分析"
                error={discovery.analysis?.error?.message ?? "执行失败"}
                onRetry={() => onRetryStep(1)}
              />
            )}

            {/* ── 步 2 · 候选评分 ── */}
            {discovery.step >= 2 && (
              <>
                {discovery.step === 2 && discovery.step_state !== "done" && <RunLine title="正在评估候选仓库" />}
                {discovery.step > 2 && discovery.candidates !== null && (
                  <ExpandDone
                    title={`发现 ${discovery.candidates.items.length} 个候选仓库`}
                    summary={
                      discovery.candidates.items.length > 0
                        ? `${discovery.candidates.items.slice(0, 5).map((c) => c.repository_name).join(" · ")}${discovery.candidates.llm_used ? "" : " · 关键词回退评分"}`
                        : "无候选"
                    }
                  >
                    {discovery.candidates.items.length === 0 ? (
                      <p className="text-[11px] text-tx3">评分没有产出候选。</p>
                    ) : (
                      <div className="flex flex-col gap-2">
                        {discovery.candidates.items.map((c) => {
                          const ratio = Math.max(0, Math.min(1, c.score <= 1 ? c.score : c.score / 100));
                          return (
                            <div key={c.repository_id} className="flex flex-col gap-0.5">
                              <div className="flex items-center gap-2">
                                <span className="font-mono text-[11px] text-tx">{c.repository_name}</span>
                                {c.is_entry_point && (
                                  <span className="flex-none rounded-full border border-line px-1.5 text-[9.5px] text-tx3">入口</span>
                                )}
                                <span className="ml-auto flex-none font-mono text-[10.5px] text-tx2">{ratio.toFixed(2)}</span>
                                <span className="h-1 w-16 flex-none overflow-hidden rounded-full bg-well">
                                  <span
                                    className="block h-full rounded-full bg-amber"
                                    style={{ width: `${Math.max(3, ratio * 100)}%` }}
                                  />
                                </span>
                              </div>
                              <p className="text-[11px] leading-[1.6] text-tx3">{c.rationale}</p>
                            </div>
                          );
                        })}
                        {!discovery.candidates.llm_used && (
                          <p className="text-[10.5px] text-tx3">以上为关键词回退评分，非模型评分。</p>
                        )}
                      </div>
                    )}
                  </ExpandDone>
                )}
                {discovery.step === 2 && discovery.step_state === "failed" && (
                  <FailLine title="候选评分" error={discovery.candidates?.error?.message ?? "执行失败"} onRetry={() => onRetryStep(2)} />
                )}
              </>
            )}

            {/* ── 步 3 · 分档审批（人审门）── */}
            {discovery.step >= 3 && (
              <>
                {discovery.step === 3 && discovery.classification === null && discovery.step_state !== "done" && (
                  <RunLine title="正在分档" />
                )}
                {discovery.classification !== null && discovery.approval.state === "not_requested" && (
                  <div className="mt-1 rounded-hard border border-line-strong bg-panel px-3 py-2">
                    <p className="text-[12px] text-tx">分档完成，请确认交付范围：</p>
                    <TierSummary classification={discovery.classification} effectiveTiers={discovery.effective_tiers} repoHosts={repoHosts} />
                    {!adjustOpen ? (
                      <div className="mt-2 flex gap-2">
                        <button
                          className="rounded-hard bg-amber px-3 py-1 text-[11.5px] font-bold text-on-amber hover:bg-amber-hi disabled:opacity-40"
                          disabled={approving || !principal}
                          onClick={() => handleApproval("approved")}
                        >
                          {approving ? "提交中…" : "按当前分档开工"}
                        </button>
                        <button
                          className="rounded-hard border border-line-strong bg-panel px-3 py-1 text-[11.5px] text-tx hover:border-amber disabled:opacity-40"
                          disabled={approving}
                          onClick={() => setAdjustOpen(true)}
                        >
                          要求调整
                        </button>
                      </div>
                    ) : (
                      <div className="mt-2">
                        <textarea
                          className="block w-full resize-none rounded-hard border border-line-strong bg-ink px-2.5 py-1.5 text-[12px] text-tx outline-none focus:border-amber"
                          rows={2}
                          placeholder="说明要怎么调整（哪些仓库进/出、为什么）"
                          value={adjustText}
                          onChange={(e) => setAdjustText(e.target.value)}
                        />
                        <div className="mt-1.5 flex gap-2">
                          <button
                            className="rounded-hard border border-line-strong bg-panel px-3 py-1 text-[11.5px] text-tx hover:border-amber disabled:opacity-40"
                            disabled={approving}
                            onClick={() => handleApproval("changes_requested")}
                          >
                            提交调整
                          </button>
                          <button className="text-[11px] text-tx3 hover:text-tx" onClick={() => setAdjustOpen(false)}>
                            取消
                          </button>
                        </div>
                      </div>
                    )}
                    {approvalError && <p className="mt-1.5 text-[11px] text-salmon">{approvalError}</p>}
                  </div>
                )}
                {discovery.approval.state !== "not_requested" && discovery.classification !== null && (
                  <ExpandDone
                    title="分档已确认"
                    summary={discovery.approval.state === "approved" ? "已批准" : "已要求改动"}
                  >
                    <div className="flex flex-col gap-1 font-mono text-[11px] text-tx2">
                      <p>必需：{tierNames(discovery.classification.required, repoHosts)}</p>
                      <p>可能：{tierNames(discovery.classification.maybe, repoHosts)}</p>
                      <p>排除：{tierNames(discovery.classification.excluded, repoHosts)}</p>
                      {discovery.classification.supplements.length > 0 && (
                        <div className="mt-0.5 flex flex-col gap-0.5 text-tx3">
                          <p>图预补充：</p>
                          {discovery.classification.supplements.map((sup) => (
                            <p key={sup.repository}>
                              {sup.repository} ← {sup.via}（{sup.confidence} ·{" "}
                              {sup.mechanism === "forward_dependencies" ? "正向依赖" : "反向依赖"}）
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  </ExpandDone>
                )}
                {discovery.step === 3 && discovery.step_state === "failed" && (
                  <FailLine title="分档审批" error={discovery.classification?.error?.message ?? "执行失败"} onRetry={() => onRetryStep(3)} />
                )}
              </>
            )}

            {/* ── 步 4 · 生成计划 ── */}
            {discovery.step >= 4 && (
              <>
                {discovery.step === 4 && discovery.step_state !== "done" && <RunLine title="正在生成计划" />}
                {discovery.step === 4 && discovery.step_state === "done" && (
                  <>
                    <ExpandDone
                      title="计划已生成"
                      summary={discovery.integration ? `${discovery.integration.task_dag_count} 个任务` : null}
                    >
                      {planBatches === null ? (
                        <p className="text-[11px] text-tx3">批次明细加载中…</p>
                      ) : planBatches.length === 0 ? (
                        <p className="text-[11px] text-tx3">该计划没有执行批次。</p>
                      ) : (
                        <div className="flex flex-col gap-1">
                          {planBatches!.map((batch, i) => (
                            <p key={i} className="text-[11px] text-tx2">
                              批次 {i + 1} · {batch.length} 仓
                              <span className="text-tx3">：{batch.join("、")}</span>
                            </p>
                          ))}
                        </div>
                      )}
                    </ExpandDone>
                    <div className="mt-1.5">
                      <button
                        className="rounded-hard bg-amber px-3.5 py-1.5 text-[11.5px] font-bold text-on-amber hover:bg-amber-hi disabled:opacity-40"
                        disabled={mBusy || !principal}
                        title={principal ? "确认后为每个仓库组建团队并开设房间，不可逆" : "决策主体未接入"}
                        onClick={handleMaterialize}
                      >
                        {mBusy ? "物化中…" : "物化并开工"}
                      </button>
                    </div>
                    {mNotReady ? (
                      <div className="mt-1.5 border-l-2 border-salmon bg-salmon-well px-2.5 py-1.5 text-[11.5px] leading-[1.7] text-salmon-hi">
                        <b className="mr-1.5 font-mono">本地 CLI 未就绪</b>
                        {mNotReady.message}
                        <ul className="mt-1">
                          {mNotReady.members.map((member) => (
                            <li key={member.agentId} className="flex flex-wrap items-baseline gap-x-2">
                              <span className={`rounded-hard border px-1.5 py-px text-[10px] ${READINESS_SKIN[member.status]}`}>
                                {READINESS_LABEL[member.status]}
                              </span>
                              <span className="font-mono text-[11px]">
                                {member.role} · {shortId(member.agentId)}
                              </span>
                              <span className="text-tx3">{member.reason}</span>
                            </li>
                          ))}
                        </ul>
                        <p className="mt-1 text-[11px]">先把它们起来（「本地 CLI」页），再点上面的按钮。</p>
                      </div>
                    ) : (
                      mError && <p className="mt-1.5 text-[11px] text-salmon" title={mError}>{mError}</p>
                    )}
                  </>
                )}
                {discovery.step === 4 && discovery.step_state === "failed" && (
                  <FailLine title="生成计划" error="执行失败" onRetry={() => onRetryStep(4)} />
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function clarifyPendingOf(discovery: DiscoveryView): boolean {
  return (
    discovery.analysis !== null &&
    !discovery.analysis.sufficient &&
    !discovery.analysis.forced_continue &&
    discovery.analysis.questions.length > 0
  );
}

function RunLine({ title }: { title: string }) {
  return (
    <p className="flex items-center gap-2 text-[12px] text-tx">
      <Loader2 size={13} strokeWidth={2} className="flex-none animate-spin text-amber" />
      <ShiningText text={`${title}…`} className="text-[12px]" />
    </p>
  );
}

/** 可展开的完成行：绿片 Check + 标题 + 收起态摘要 + 展开箭头；
 *  展开区用 grid-rows 过渡（300ms），内容左缘以细竖线对齐到标题。 */
function ExpandDone({
  title,
  summary,
  children,
}: {
  title: string;
  summary: string | null;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="text-[12px] leading-[1.6]">
      <button
        className="flex w-full items-start gap-2 text-left"
        onClick={() => setOpen((v) => !v)}
        title={open ? "收起" : "展开详情"}
      >
        <span className="mt-[2px] grid size-[16px] flex-none place-items-center rounded-full bg-olive/10 text-olive">
          <Check size={11} strokeWidth={2.5} />
        </span>
        <span className="flex-none text-tx">{title}</span>
        <span className={`min-w-0 flex-1 truncate pt-px text-right text-[11px] text-tx3 ${open ? "opacity-0" : ""}`}>
          {summary}
        </span>
        <ChevronDown
          size={12}
          strokeWidth={1.5}
          className={`mt-[2px] flex-none text-tx3 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      <div
        className={`grid transition-[grid-template-rows,opacity] duration-300 ease-in-out ${
          open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="ml-1 mt-1.5 border-l border-line pl-4">{children}</div>
        </div>
      </div>
    </div>
  );
}

/** 需求分析展开区：关键词胶囊 + 待澄清问题与已提交的回答。 */
function KeywordDetail({
  analysis,
}: {
  analysis: NonNullable<DiscoveryView["analysis"]>;
}) {
  const dimensions = analysis.dimensions ?? [];
  return (
    <div className="flex flex-col gap-1.5">
      {dimensions.length > 0 && (
        <div className="flex flex-col gap-0.5">
          {dimensions.map((d) => (
            <p key={d.name} className="flex items-start gap-1.5 text-[11px] leading-[1.6] text-tx2">
              {d.covered ? (
                <Check size={11} strokeWidth={2.5} className="mt-[3px] flex-none text-olive" />
              ) : (
                <X size={11} strokeWidth={2.5} className="mt-[3px] flex-none text-salmon" />
              )}
              <span>
                {d.name}：{d.note || (d.covered ? "已说清" : "缺失")}
              </span>
            </p>
          ))}
        </div>
      )}
      {analysis.extracted_keywords.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {analysis.extracted_keywords.slice(0, 8).map((k) => (
            <span key={k} className="rounded-full border border-line bg-well px-2 py-px text-[10.5px] text-tx2">
              {k}
            </span>
          ))}
        </div>
      )}
      {analysis.questions.length > 0 && (
        <div className="flex flex-col gap-1">
          {analysis.questions.map((q, i) => {
            const answered = analysis.answers.find((a) => a.question === q);
            return (
              <p key={q} className="text-[11px] leading-[1.6] text-tx2">
                问 {i + 1}：{q}
                {answered && <span className="text-tx">—— 答：{answered.answer}</span>}
              </p>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** 三档名单的措辞（含地址 host）；与审批盒里的 TierSummary 同一条格式。 */
function tierNames(list: Array<{ repository: string }>, repoHosts: Record<string, string>): string {
  if (list.length === 0) return "无";
  return list
    .map((item) => {
      const host = repoHosts[item.repository];
      return host ? `${item.repository}（${host}）` : item.repository;
    })
    .join(" · ");
}

function FailLine({ title, error, onRetry }: { title: string; error: string; onRetry: () => void }) {
  return (
    <div className="flex items-start gap-2 text-[12px] leading-[1.6]">
      <span className="mt-[2px] grid size-[16px] flex-none place-items-center rounded-full bg-salmon/10 text-salmon">
        <X size={11} strokeWidth={2.5} />
      </span>
      <div className="min-w-0">
        <p className="text-salmon">
          {title}失败
          <button className="ml-2 text-[11px] text-tx2 underline hover:text-tx" onClick={onRetry}>
            重试
          </button>
        </p>
        <p className="truncate text-[11px] text-tx3" title={error}>
          {error}
        </p>
      </div>
    </div>
  );
}

/** 三档摘要：名字顿号一行，数量在措辞里；不渲染完整分档表（要求调整时按理由提交）。 */
function TierSummary({
  classification,
  effectiveTiers,
  repoHosts,
}: {
  classification: DiscoveryClassificationBlock;
  effectiveTiers: DiscoveryView["effective_tiers"];
  repoHosts: Record<string, string>;
}) {
  const names = (list: Array<{ repository: string }>) =>
    list.length === 0
      ? "无"
      : list
          .map((item) => {
            const host = repoHosts[item.repository];
            return host ? `${item.repository}（${host}）` : item.repository;
          })
          .join(" · ");
  return (
    <div className="mt-1 grid gap-0.5 font-mono text-[11px] text-tx2">
      <p>必需：{names(classification.required)}</p>
      <p>可能：{names(classification.maybe)}</p>
      <p>排除：{names(classification.excluded)}</p>
      {effectiveTiers.length === 0 && <p className="text-tx3">本次分档没有产出生效档位（三档全空）。</p>}
    </div>
  );
}
