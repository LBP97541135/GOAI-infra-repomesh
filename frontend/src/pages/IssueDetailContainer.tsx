import { useCallback, useEffect, useRef, useState } from "react";
import type { IssueDetailView, IssueRoundView, RoomListItemView } from "../api/contract";
import type { ApprovalInfo, Decision } from "../types";
import {
  archiveRound,
  fetchDecisionDeck,
  fetchRoundDecisionHistory,
  resolveGovernanceAgent,
  submitGovernanceDecision,
  type GovernanceAgent,
} from "../api/decisions";
import type { RoundHistoryState } from "../components/RoundsPanel";
import type { DeliveryAggregate } from "../api/contract";
import type { EvidenceView } from "../types";
import { fetchIssueDetail, fetchRooms } from "../api/rooms";
import { resolveDataSourceMode } from "../api/source";
import { ApprovalModal } from "../components/ApprovalModal";
import { EvidenceModal } from "../components/EvidenceModal";
import { approvalForDecision, evidenceFromAggregate } from "../viewmodel";
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
        setError(err instanceof Error ? err.message : String(err));
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
    const roundLabel = roundIndex >= 0 ? `第 ${roundIndex + 1} 轮` : `轮次 ${roundId.slice(0, 8)}`;

    fetchDecisionDeck(roundId)
      .then((data) => {
        if (cancelled) return;
        setDeck(data.deck);
        // S1：授权单不再在取数时预绑定「第一个 approve 项」——由点击的卡即时构建
        setApproval(null);
        setDeckAggregate(data.aggregate);
        setDeckNote(
          // 夹具已与详情/房间同源（同一 issue 同一轮），所以只需说明这是回放数据，
          // 不必再声明「非本 issue」——那句是借用 v1 演示交付时期的补丁
          replay ? `${roundLabel} · 回放夹具` : `${roundLabel} · live`,
        );
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setDeck([]);
        setApproval(null);
        setDeckAggregate(null);
        setDeckNote(`${roundLabel} · 决策取用失败：${err instanceof Error ? err.message : String(err)}`);
      });
    return () => {
      cancelled = true;
    };
  }, [roundId, detail, reload]);

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
              error: err instanceof Error ? err.message : String(err),
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
          onToast(`归档失败：${err instanceof Error ? err.message : String(err)}`);
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
        setApprovalError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setApprovalSubmitting(false));
  };

  if (loading) {
    return (
      <div className="max-w-[860px]">
        <button className="pb-3 text-[11.5px] text-tx2 hover:text-tx" onClick={onBack}>
          ‹ issue
        </button>
        <p className="py-8 text-center text-[12.5px] text-tx2">加载中…</p>
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="max-w-[860px]">
        <button className="pb-3 text-[11.5px] text-tx2 hover:text-tx" onClick={onBack}>
          ‹ issue
        </button>
        <div className="rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3">
          <div className="eyebrow mb-1 text-salmon">issue 详情加载失败</div>
          <p className="text-[12px] text-salmon">{error ?? "未取到详情"}</p>
          <button
            className="mt-2 rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
            onClick={() => setReload((n) => n + 1)}
          >
            重试
          </button>
        </div>
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
