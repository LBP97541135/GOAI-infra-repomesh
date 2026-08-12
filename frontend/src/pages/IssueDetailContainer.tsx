import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DeliveryAggregate, IssueDetailView, IssueRoundView, RoomListItemView } from "../api/contract";
import type { ApprovalInfo, Decision, EvidenceView, PlanAnchor } from "../types";
import {
  archiveRound,
  fetchDecisionDeck,
  fetchRoundDecisionHistory,
  resolveGovernanceAgent,
  submitGovernanceDecision,
  type GovernanceAgent,
} from "../api/decisions";
import type { RoundHistoryState } from "../components/RoundsPanel";
import { fetchIssueDetail, fetchRepositoryPlan, fetchRooms } from "../api/rooms";
import { ApiError } from "../api/client";
import { resolveDataSourceMode } from "../api/source";
import { ApprovalModal } from "../components/ApprovalModal";
import { EvidenceModal } from "../components/EvidenceModal";
import type { PlanDagState } from "../components/PlanDagPanel";
import { ErrorPanel, LoadingLine } from "../components/StatusBlocks";
import { errText, shortId } from "../display";
import { approvalForDecision, dagExecutionFromAggregate, evidenceFromAggregate } from "../viewmodel";
import { IssueDetailPage } from "./IssueDetailPage";

/** issue 详情取数容器（§3 概览 + §5.1 房间清单 + §4.3 决策夹 + §4.4 写回路）。
 *
 *  空房间清单**不是错误**：未建团的 issue 返回 `{"rooms": []}` 且 HTTP 200。
 *  决策夹是轮次粒度，取 `active_round_id ?? latest_round_id`；决策取用失败不该
 *  拖垮整页——概览与房间照常渲染，决策区单独降级。 */
export function IssueDetailContainer({
  issueId,
  onBack,
  onOpenRoom,
  onToast,
}: {
  issueId: string;
  onBack: () => void;
  onOpenRoom: (room: RoomListItemView) => void;
  onToast: (text: string) => void;
}) {
  const [detail, setDetail] = useState<IssueDetailView | null>(null);
  const [rooms, setRooms] = useState<RoomListItemView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  const [deck, setDeck] = useState<Decision[]>([]);
  const [deckHidden, setDeckHidden] = useState(false);
  const [deckNote, setDeckNote] = useState<string | null>(null);
  const [approval, setApproval] = useState<ApprovalInfo | null>(null);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalSubmitting, setApprovalSubmitting] = useState(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  /** 治理决策主体：从花名册派生（主脑裁决乙案）。null = 尚未解析或解析不到，
   *  后者必须禁用提交——发一个查无此人的 decided_by_agent_id 只会换来 403。 */
  const [principal, setPrincipal] = useState<GovernanceAgent | null>(null);
  const [principalResolving, setPrincipalResolving] = useState(true);

  /** 轮次区（B-6）：展开态与逐轮决策取数结果。懒取——首次展开才发请求，
   *  已取到的轮次收起再展开不重取（issue 或 reload 变化时整体清空）。
   *  代际号防 A6：清空后在途响应落回旧数据且不再重取。 */
  const [roundsExpanded, setRoundsExpanded] = useState<Record<string, boolean>>({});
  const [roundsHistory, setRoundsHistory] = useState<Record<string, RoundHistoryState>>({});
  const roundsEpoch = useRef(0);

  /** 轮次归档（B-4）：两步确认——第一次点击进入确认态，再点才发请求；
   *  点击其他轮次或成功/失败后确认态复位。 */
  const [archiveConfirmId, setArchiveConfirmId] = useState<string | null>(null);
  const [archivingId, setArchivingId] = useState<string | null>(null);

  // B5：「确认归档？」不能永久驻留——用户点了第一步后转头看别的，几分钟后
  // 误触同一按钮就是真归档。8 秒无第二击自动复位回「归档本轮」。
  useEffect(() => {
    if (!archiveConfirmId) return;
    const id = window.setTimeout(() => setArchiveConfirmId(null), 8000);
    return () => window.clearTimeout(id);
  }, [archiveConfirmId]);

  /** 计划 DAG 面板（C-2）：契约 §5.4 的计划纸面。取数失败 / 无快照都不该拖垮整页，
   *  所以自成一态，与决策夹同款单区块降级。 */
  const [planState, setPlanState] = useState<PlanDagState>({ status: "loading" });
  const [planReload, setPlanReload] = useState(0);

  /** 锚点回退的中转站：发现面板报上来的候选锚点。三态有意义——
   *  `undefined` = 发现读投影还没落定（**还没问过**），`null` = 问过、没有候选，
   *  对象 = 有候选。把前两者压成一个 null 会让草稿 issue 一进页面就先闪一屏
   *  「尚未确定范围」，然后再跳成图。 */
  const [candidateAnchor, setCandidateAnchor] = useState<PlanAnchor | null | undefined>(undefined);

  /** 只跟 issueId 清空，**不跟 reload**：容器刷新不会让发现面板重取（它有自己的
   *  reload 计数），跟着 reload 清就会把锚点永久卡在「还没问过」，DAG 面板恒转圈。 */
  useEffect(() => setCandidateAnchor(undefined), [issueId]);

  // 稳定引用：发现面板把它存进 ref，内联箭头函数每次 render 换 identity 会让下游空转
  const handleCandidateAnchor = useCallback((anchor: PlanAnchor | null) => setCandidateAnchor(anchor), []);

  /** 证据面（B-3）：决策夹取数时保留的本轮聚合 + 当前打开的单仓证据 */
  const [deckAggregate, setDeckAggregate] = useState<DeliveryAggregate | null>(null);
  const [evidence, setEvidence] = useState<EvidenceView | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    // 换 issue / 手动刷新时清空轮次区缓存：真批后决策集合已变，旧缓存是过期事实
    roundsEpoch.current += 1;
    setRoundsExpanded({});
    setRoundsHistory({});
    Promise.all([fetchIssueDetail(issueId), fetchRooms(issueId)])
      .then(([d, r]) => {
        if (cancelled) return;
        setDetail(d);
        setRooms(r);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(errText(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [issueId, reload]);

  const roundId = detail?.active_round_id ?? detail?.latest_round_id ?? null;
  const organizationId = detail?.organization_id ?? null;

  // 决策主体按 issue 所属组织解析：跨组织的 leader 会被后端以「belongs to another
  // organization」拒绝，所以这里就得选对，不能等提交时才发现
  useEffect(() => {
    if (!detail) return;
    let cancelled = false;
    setPrincipalResolving(true);
    resolveGovernanceAgent(organizationId)
      .then((agent) => !cancelled && setPrincipal(agent))
      .catch(() => !cancelled && setPrincipal(null))
      .finally(() => !cancelled && setPrincipalResolving(false));
    return () => {
      cancelled = true;
    };
  }, [detail, organizationId]);

  useEffect(() => {
    if (!roundId || !detail) {
      setDeck([]);
      setDeckNote(null);
      setApproval(null);
      setDeckAggregate(null); // 切到草稿 issue 时不留上一 issue 的聚合
      return;
    }
    let cancelled = false;
    const replay = resolveDataSourceMode() === "replay";
    const roundIndex = detail.rounds.findIndex((r) => r.round_id === roundId);
    const roundLabel = roundIndex >= 0 ? `第 ${roundIndex + 1} 轮` : `轮次 ${shortId(roundId)}`;

    fetchDecisionDeck(roundId)
      .then((data) => {
        if (cancelled) return;
        setDeck(data.deck);
        // S1：授权单不再在取数时预绑定「第一个 approve 项」——由点击的卡即时构建
        setApproval(null);
        setDeckAggregate(data.aggregate);
        setDeckNote(
          // 夹具与详情/房间同源（同一 issue 同一轮），只需说明这是回放数据
          replay ? `${roundLabel} · 回放夹具` : `${roundLabel} · live`,
        );
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setDeck([]);
        setApproval(null);
        setDeckAggregate(null);
        setDeckNote(`${roundLabel} · 决策取用失败：${errText(err)}`);
      });
    return () => {
      cancelled = true;
    };
    // B2：依赖 detail.rounds 而非 detail 整体——reload 时 detail 对象 identity 必变，
    // 依赖整个对象会让每次刷新多发一对 getDelivery+getDecisions（4 请求应为 2）。
    // roundLabel 只消费 rounds，够用。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roundId, detail?.rounds, reload]);

  /** 计划纸面的锚点仓。§5.4 端点是**单仓作用域**，而 DAG 与 execution_batches 是
   *  issue 级、每个仓取回的是同一份（服务端只用 repository_id 决定哪个节点带
   *  `is_focus` 和取哪一份 spec）——所以画整张图只需要任取一个本 issue 的仓库。
   *
   *  **两条来路，按可信度排序**：
   *   1. issue 拓扑 `detail.repositories[0]`——范围已冻结时的事实源；
   *   2. 发现链候选块（发现面板报上来）——发现走完但**尚未物化**时拓扑还是空的，
   *      此时只有候选块认识本 issue 域内的仓库。缺了这条回退，「计划已在快照里、
   *      DAG 面板却恒显尚未确定范围」就是本批开工前实走撞到的那个洞。
   *  两条都空才是真的没有锚点。 */
  const scopeAnchor = detail?.repositories[0] ?? null;
  const anchorFromCandidate = scopeAnchor === null;
  const anchorRepositoryId = scopeAnchor?.repository_id ?? candidateAnchor?.repositoryId ?? null;
  const anchorRepositoryName = scopeAnchor?.name ?? candidateAnchor?.name ?? null;
  /** 拓扑没有、发现链还没落定：此刻「没有锚点」尚未成立，不能就宣布 absent。 */
  const anchorPending = scopeAnchor === null && candidateAnchor === undefined;
  const hasDetail = detail !== null;

  useEffect(() => {
    if (!hasDetail) return;
    if (anchorPending) {
      // 还在等发现读投影。这一态是「不知道」，不是「没有」。
      setPlanState({ status: "loading" });
      return;
    }
    if (!anchorRepositoryId || !anchorRepositoryName) {
      // 拓扑与候选块都空 → 没有仓库可作锚点，端点无从调用。空要说出来。
      setPlanState({
        status: "absent",
        reason:
          "尚未确定交付范围，发现链也还没有候选仓库——两处都取不到锚点仓，" +
          "而 §5.4 计划纸面是按仓取数的，端点无从调用。",
      });
      return;
    }
    let cancelled = false;
    setPlanState({ status: "loading" });
    fetchRepositoryPlan(issueId, anchorRepositoryId)
      .then(
        (plan) =>
          !cancelled &&
          setPlanState({ status: "ready", plan, anchorName: anchorRepositoryName, anchorFromCandidate }),
      )
      .catch((err: unknown) => {
        if (cancelled) return;
        // 404 = 无计划快照。服务端把「issue 不存在」与「issue 从未规划」写成同一个
        // 404，但此处 issue 详情已经取到了，所以只可能是后者——不是错误态。
        setPlanState(
          err instanceof ApiError && err.status === 404
            ? {
                status: "absent",
                reason: anchorFromCandidate
                  ? `以候选仓 ${anchorRepositoryName} 作回退锚点取计划纸面，服务端返回 404：` +
                    "要么本 issue 还没有计划快照，要么该候选不在计划范围内——服务端把两者写成同一个 404，界面无从分辨。"
                  : "本 issue 还没有计划快照，DAG 无从绘制（计划由发现链在分档审批后生成）。",
              }
            : { status: "error", message: errText(err) },
        );
      });
    return () => {
      cancelled = true;
    };
    // 依赖只列锚点标识与刷新计数，不列 detail 整体：reload 时 detail 的 identity 必变，
    // 依赖整个对象会让每次刷新多发一次计划请求（B2 的同款教训）。
  }, [
    issueId,
    hasDetail,
    anchorRepositoryId,
    anchorRepositoryName,
    anchorFromCandidate,
    anchorPending,
    reload,
    planReload,
  ]);

  /** DAG 执行态着色（C-4）的输入。数据源是**本轮交付聚合**——决策夹取数时已经把它
   *  留下了（`deckAggregate`），这里零额外请求。
   *
   *  `null` 有两种来路，页脚都能说清：草稿 issue 没有轮次（聚合根本没取）、
   *  或本轮聚合取用失败。两种都是「无执行事实」，节点维持结构三视觉——
   *  取不到就不上色，比摆一排灰块让人以为「全都在等」诚实。
   *
   *  轮次标签与决策夹同一份算法（第 N 轮 / 轮次 短id），免得同一页出现两种叫法。 */
  const roundIndex = roundId && detail ? detail.rounds.findIndex((r) => r.round_id === roundId) : -1;
  const roundLabel = !roundId ? "" : roundIndex >= 0 ? `第 ${roundIndex + 1} 轮` : `轮次 ${shortId(roundId)}`;
  const planExecution = useMemo(
    () => (deckAggregate ? dagExecutionFromAggregate(deckAggregate, roundLabel) : null),
    [deckAggregate, roundLabel],
  );

  /** 物化确认弹窗（C-3）里的 M。设计定稿写死「**每仓一队**」，所以数的是计划里的
   *  **仓库**——`execution_batches` 去重后的仓库名数，不是 `dag.nodes.length`
   *  （同一仓库在多个批次里出现就会被数两遍），也不是候选数。
   *
   *  计划纸面没取到时为 null：弹窗照实说「取不到」，不拿别的数顶替一个看着像的数字。 */
  const planRepositoryCount =
    planState.status === "ready" ? new Set(planState.plan.execution_batches.flat()).size : null;

  /** catalog 查无仓库的节点数。>0 时上面那个 M 与服务端实际建队数可能不等
   *  （要不要为一个查无此仓的名字建队是服务端的判断），弹窗里如实旁注。 */
  const planUnresolvedCount =
    planState.status === "ready"
      ? planState.plan.dag.nodes.filter((n) => n.repository_id === null).length
      : 0;

  /** 物化成功后刷整页：轮次、房间、关联仓库、DAG 着色全在这一次写里变了，
   *  只刷发现面板会让页面上半截是新事实、下半截还是物化前的旧图。 */
  const handleMaterialized = useCallback(() => {
    setReload((n) => n + 1);
    setPlanReload((n) => n + 1);
  }, []);

  /** 计划纸面重取。**必须是稳定引用**：发现面板把它存进 ref 之外还会随 issue 变化
   *  重建轮询，内联箭头函数每次 render 换 identity 会让下游的 effect 白白重跑。 */
  const handlePlanReload = useCallback(() => setPlanReload((n) => n + 1), []);

  const handleToggleRound = useCallback(
    (round: IssueRoundView) => {
      const id = round.round_id;
      const willExpand = !roundsExpanded[id];
      setRoundsExpanded((prev) => ({ ...prev, [id]: willExpand }));

      // 懒取数：只在展开且无成功缓存时取；失败态在下次展开时重取。
      // 取数完成前用户可能已收起，结果照常落桶——再展开即命中缓存，无副作用。
      const existing = roundsHistory[id];
      if (!willExpand || (existing && !existing.loading && !existing.error)) return;
      const epoch = roundsEpoch.current;
      setRoundsHistory((prev) => ({ ...prev, [id]: { loading: true, error: null, pending: [], recorded: [] } }));
      fetchRoundDecisionHistory(id)
        .then((data) => {
          if (epoch !== roundsEpoch.current) return; // A6：缓存已整体清空，旧响应不落桶
          setRoundsHistory((p) => ({
            ...p,
            [id]: { loading: false, error: null, pending: data.pending, recorded: data.recorded },
          }));
        })
        .catch((err: unknown) => {
          if (epoch !== roundsEpoch.current) return;
          setRoundsHistory((p) => ({
            ...p,
            [id]: {
              loading: false,
              error: errText(err),
              pending: [],
              recorded: [],
            },
          }));
        });
    },
    [roundsExpanded, roundsHistory],
  );

  const handleArchiveRound = useCallback(
    (round: IssueRoundView) => {
      if (archiveConfirmId !== round.round_id) {
        setArchiveConfirmId(round.round_id);
        return;
      }
      if (resolveDataSourceMode() === "replay") {
        setArchiveConfirmId(null);
        onToast("已归档（回放演示，未写入后端）");
        return;
      }
      setArchivingId(round.round_id);
      archiveRound(round.round_id)
        .then(() => {
          setArchiveConfirmId(null);
          onToast("本轮已归档（轮次级；issue 的 Open/Closed 仍按 §2.1 派生）");
          setReload((n) => n + 1);
        })
        .catch((err: unknown) => {
          // 409 = 活跃轮次拒绝归档等，原因原样呈现，不静默
          setArchiveConfirmId(null);
          onToast(`归档失败：${errText(err)}`);
        })
        .finally(() => setArchivingId(null));
    },
    [archiveConfirmId, onToast],
  );

  const handleDecisionAction = useCallback(
    (decision: Decision, actionIdx: number) => {
      const action = decision.actionKinds?.[actionIdx];
      if (action === "approve_merge") {
        // S1：授权单绑定**这张卡**的仓库与 SHA。多仓同时待批时绝不能拿别的卡顶替
        if (!deckAggregate) {
          onToast("授权单不可用：本轮聚合未取到");
          return;
        }
        const built = approvalForDecision(deckAggregate, decision);
        if (!built) {
          onToast("授权单不可用：该决策未指向仓库");
          return;
        }
        setApproval(built);
        setApprovalError(null);
        setApprovalOpen(true);
        return;
      }
      // 证据面（B-3 最小版）：从本轮聚合切该决策指向仓库的既有证据
      if (action === "view_evidence") {
        if (!deckAggregate || !decision.repositoryId) {
          onToast("证据不可用：本轮聚合未取到或决策未指向仓库");
          return;
        }
        setEvidence(evidenceFromAggregate(deckAggregate, decision.repositoryId));
        setEvidenceOpen(true);
      }
    },
    [onToast, deckAggregate],
  );

  const handleApprove = (comment: string) => {
    if (!roundId || !approval) return;
    if (resolveDataSourceMode() === "replay") {
      setApprovalOpen(false);
      // S1：只消化被批准的那一张卡，不整类抹除
      setDeck((prev) => prev.filter((x) => x.id !== approval.decisionId));
      onToast("已批准（回放演示，未写入后端）");
      return;
    }
    // 兜底：按钮此时应当已被禁用，这里只是不让任何路径漏发注定被拒的请求
    if (!principal) {
      setApprovalError("决策主体未接入，无法提交。");
      return;
    }
    setApprovalSubmitting(true);
    setApprovalError(null);
    submitGovernanceDecision(roundId, approval, comment, principal.agentId)
      .then(() => {
        setApprovalOpen(false);
        onToast("治理决策已记录：READY（head-bound），merge gate 放行");
        setReload((n) => n + 1);
      })
      .catch((err: unknown) => {
        // 409 = head 漂移，必须显示在弹窗内，不静默失败
        setApprovalError(errText(err));
      })
      .finally(() => setApprovalSubmitting(false));
  };

  if (loading) {
    return (
      <div className="max-w-[860px]">
        <button className="pb-3 text-[11.5px] text-tx2 hover:text-tx" onClick={onBack}>
          ‹ issue
        </button>
        <LoadingLine />
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="max-w-[860px]">
        <button className="pb-3 text-[11.5px] text-tx2 hover:text-tx" onClick={onBack}>
          ‹ issue
        </button>
        <ErrorPanel
          className=""
          title="issue 详情加载失败"
          message={error ?? "未取到详情"}
          onRetry={() => setReload((n) => n + 1)}
        />
      </div>
    );
  }

  return (
    <>
      <IssueDetailPage
        detail={detail}
        rooms={rooms}
        deck={deck}
        deckHidden={deckHidden}
        deckNote={deckNote}
        onToggleDeck={() => setDeckHidden((v) => !v)}
        onBringToFront={(id) =>
          setDeck((prev) => {
            const found = prev.find((d) => d.id === id);
            return found ? [...prev.filter((d) => d.id !== id), found] : prev;
          })
        }
        onDecisionAction={handleDecisionAction}
        planState={planState}
        planExecution={planExecution}
        onRetryPlan={handlePlanReload}
        onPlanGenerated={handlePlanReload}
        onCandidateAnchor={handleCandidateAnchor}
        materialize={{
          roundCount: detail.rounds.length,
          planRepositoryCount,
          planUnresolvedCount,
        }}
        onMaterialized={handleMaterialized}
        onBack={onBack}
        onOpenRoom={onOpenRoom}
        onToast={onToast}
        roundsExpanded={roundsExpanded}
        roundsHistory={roundsHistory}
        onToggleRound={handleToggleRound}
        archiveConfirmId={archiveConfirmId}
        archivingId={archivingId}
        onArchiveRound={handleArchiveRound}
      />
      <ApprovalModal
        open={approvalOpen}
        info={approval}
        submitting={approvalSubmitting}
        errorText={approvalError}
        principal={
          resolveDataSourceMode() === "replay"
            ? { state: "replay", label: "回放演示（不写后端）" }
            : principalResolving
              ? { state: "resolving", label: "解析中…" }
              : principal
                ? { state: "ready", label: `AGENT ${principal.label}` }
                : { state: "missing", label: "决策主体未接入" }
        }
        onCancel={() => setApprovalOpen(false)}
        onApprove={handleApprove}
      />
      <EvidenceModal
        open={evidenceOpen}
        roundLabel={deckNote ?? ""}
        evidence={evidence}
        onClose={() => setEvidenceOpen(false)}
      />
    </>
  );
}
