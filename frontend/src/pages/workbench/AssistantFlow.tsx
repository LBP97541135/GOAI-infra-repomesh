import { useState } from "react";
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
import { fetchPolicyDraft } from "../../api/humanControl";
import { resolveDataSourceMode } from "../../api/source";
import type { GovernanceAgent } from "../../api/decisions";
import type { ApprovalPrincipal } from "../../components/DiscoveryApproval";
import { MaterializeModal } from "../../components/MaterializeModal";
import type { PolicyDraftState } from "../../components/SupervisionPolicyCard";
import { ShiningText } from "../../components/ui/shining-text";
import { errText } from "../../display";
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
  principalResolving,
  materialize,
  clarifySending,
  onAdvanced,
  onRetryStep,
  onToast,
}: {
  detail: IssueDetailView;
  /** 发现读投影（外层 2.5s 轮询）；null = 还没取到 */
  discovery: DiscoveryView | null;
  principal: GovernanceAgent | null;
  principalResolving: boolean;
  /** 物化确认弹窗的 M/N 上下文（来自 useIssueFlowState） */
  materialize: { roundCount: number; planRepositoryCount: number | null; planUnresolvedCount: number };
  /** 追问回答发送中：输入框与按钮置灰的依据（状态在外层） */
  clarifySending: boolean;
  /** 任何写成功后让外层整轮刷新 */
  onAdvanced: () => void;
  /** 失败重试：清防重发表 + 外层刷新，驱动器会用新键重发 */
  onRetryStep: (step: 1 | 2 | 3 | 4) => void;
  onToast: (text: string) => void;
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

  // ── 物化开工（门 2）──
  const [mOpen, setMOpen] = useState(false);
  const [mBusy, setMBusy] = useState(false);
  const [mError, setMError] = useState<string | null>(null);
  const [mNotReady, setMNotReady] = useState<ExternalMembersNotReadyDetail | null>(null);
  const [policy, setPolicy] = useState<PolicyDraftState>({ kind: "loading" });
  const openMaterialize = () => {
    setMOpen(true);
    setMError(null);
    if (replay) {
      setPolicy({ kind: "unset" });
      return;
    }
    // 档案在物化后锁死，这是最后一次看到草稿的机会——弹窗旁边必须摆出来。
    // 404 = 从未设定（「未设定」是事实不是错误）；401 会话过期不重试。
    fetchPolicyDraft(detail.issue_id)
      .then((draft) => setPolicy({ kind: "set", draft }))
      .catch((err: unknown) => {
        const status = err && typeof err === "object" && "status" in err ? (err as { status: number }).status : 0;
        setPolicy(status === 404 ? { kind: "unset" } : { kind: "unset" });
      });
  };
  const handleMaterialize = () => {
    if (replay) {
      setMError("回放模式不写后端：物化会真实建团队、开房间。加 ?source=live 后可真实执行。");
      return;
    }
    if (!principal) {
      setMError("决策主体未接入，无法物化。");
      return;
    }
    setMBusy(true);
    setMError(null);
    materializeDiscovery(detail.issue_id, {
      created_by_agent_id: principal.agentId,
      idempotency_key: newIdempotencyKey("materialize"),
    })
      .then(() => {
        setMOpen(false);
        onToast("已物化开工：团队与房间组建中，轮次会流进对话");
        onAdvanced();
      })
      .catch((err: unknown) => {
        setMNotReady(externalMembersNotReady(err));
        setMError(errText(err));
      })
      .finally(() => setMBusy(false));
  };

  const approvalPrincipal: ApprovalPrincipal = replay
    ? { state: "replay", label: "回放演示（不写后端）" }
    : principalResolving
      ? { state: "resolving", label: "解析中…" }
      : principal
        ? { state: "ready", label: `AGENT ${principal.label}` }
        : { state: "missing", label: "决策主体未接入" };

  return (
    <div className="flex gap-2">
      {/* ZCode 对话式：一个小图标，右边就是信息——没有身份、没有名字、没有头像框 */}
      <span className="flex-none pt-[3px] text-[11px] leading-none text-amber">✦</span>
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
            {(discovery.step > 1 || (discovery.step === 1 && discovery.step_state === "done" && !clarifyPendingOf(discovery))) && (
              <DoneLine
                title="需求已解析"
                summary={
                  discovery.analysis && discovery.analysis.extracted_keywords.length > 0
                    ? `关键词：${discovery.analysis.extracted_keywords.slice(0, 6).join("・")}`
                    : null
                }
              />
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
                  <DoneLine
                    title={`发现 ${discovery.candidates.items.length} 个候选仓库`}
                    summary={
                      discovery.candidates.items.length > 0
                        ? `${discovery.candidates.items.slice(0, 5).map((c) => c.repository_name).join(" · ")}${discovery.candidates.llm_used ? "" : " · 关键词回退评分"}`
                        : "无候选"
                    }
                  />
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
                    <TierSummary classification={discovery.classification} effectiveTiers={discovery.effective_tiers} />
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
                {discovery.approval.state !== "not_requested" && (
                  <DoneLine
                    title="分档已确认"
                    summary={discovery.approval.state === "approved" ? "已批准" : "已要求改动"}
                  />
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
                    <DoneLine title="计划已生成" summary={discovery.integration ? `${discovery.integration.task_dag_count} 个任务` : null} />
                    <div className="mt-1.5">
                      <button
                        className="rounded-hard bg-amber px-3.5 py-1.5 text-[11.5px] font-bold text-on-amber hover:bg-amber-hi disabled:opacity-40"
                        disabled={!principal}
                        title={principal ? "确认后为每个仓库组建团队并开设房间" : "决策主体未接入"}
                        onClick={openMaterialize}
                      >
                        物化并开工
                      </button>
                    </div>
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

      <MaterializeModal
        open={mOpen}
        issueId={detail.issue_id}
        planVersion={discovery?.plan_version ?? 0}
        taskCount={discovery?.integration?.task_dag_count ?? 0}
        teamCount={materialize.planRepositoryCount}
        unresolvedCount={materialize.planUnresolvedCount}
        policy={policy}
        principal={approvalPrincipal}
        submitting={mBusy}
        errorText={mError}
        notReady={mNotReady}
        onCancel={() => setMOpen(false)}
        onConfirm={handleMaterialize}
      />
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
      <i className="blink inline-block size-[5px] flex-none rounded-full bg-amber not-italic" />
      <ShiningText text={`${title}…`} className="text-[12px]" />
    </p>
  );
}

function DoneLine({ title, summary }: { title: string; summary: string | null }) {
  return (
    <div className="text-[12px] leading-[1.6]">
      <p className="text-tx">
        <span className="mr-1.5 text-olive">✓</span>
        {title}
      </p>
      {summary && <p className="min-w-0 truncate pl-[18px] text-[11px] text-tx3">{summary}</p>}
    </div>
  );
}

function FailLine({ title, error, onRetry }: { title: string; error: string; onRetry: () => void }) {
  return (
    <div className="text-[12px] leading-[1.6]">
      <p className="text-salmon">
        <span className="mr-1.5">✕</span>
        {title}失败
        <button className="ml-2 text-[11px] text-tx2 underline hover:text-tx" onClick={onRetry}>
          重试
        </button>
      </p>
      <p className="min-w-0 truncate pl-[18px] text-[11px] text-tx3" title={error}>
        {error}
      </p>
    </div>
  );
}

/** 三档摘要：名字顿号一行，数量在措辞里；不渲染完整分档表（要求调整时按理由提交）。 */
function TierSummary({
  classification,
  effectiveTiers,
}: {
  classification: DiscoveryClassificationBlock;
  effectiveTiers: DiscoveryView["effective_tiers"];
}) {
  const names = (list: Array<{ repository: string }>) =>
    list.length === 0 ? "无" : list.map((item) => item.repository).join(" · ");
  return (
    <div className="mt-1 grid gap-0.5 font-mono text-[11px] text-tx2">
      <p>必需：{names(classification.required)}</p>
      <p>可能：{names(classification.maybe)}</p>
      <p>排除：{names(classification.excluded)}</p>
      {effectiveTiers.length === 0 && <p className="text-tx3">本次分档没有产出生效档位（三档全空）。</p>}
    </div>
  );
}
