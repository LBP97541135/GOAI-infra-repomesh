import { useCallback, useEffect, useState } from "react";
import type { IssueDetailView, RoomListItemView } from "../api/contract";
import type { ApprovalInfo, Decision } from "../types";
import {
  fetchDecisionDeck,
  resolveGovernanceAgent,
  submitGovernanceDecision,
  type GovernanceAgent,
} from "../api/decisions";
import { fetchIssueDetail, fetchRooms } from "../api/rooms";
import { resolveDataSourceMode } from "../api/source";
import { ApprovalModal } from "../components/ApprovalModal";
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

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
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
        setApproval(data.approval);
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
        setDeckNote(`${roundLabel} · 决策取用失败：${err instanceof Error ? err.message : String(err)}`);
      });
    return () => {
      cancelled = true;
    };
  }, [roundId, detail, reload]);

  const handleDecisionAction = useCallback(
    (decision: Decision, actionIdx: number) => {
      const action = decision.actionKinds?.[actionIdx];
      if (action === "approve_merge") {
        setApprovalError(null);
        setApprovalOpen(true);
        return;
      }
      // 证据面本就是既有缺口；原文案把人指向 v1 控制台，v1 退役后如实说明缺口
      onToast("证据面（CI 报告 / 变更详情）未接入。门禁与变更可在房间视图的环境窗查看");
    },
    [onToast],
  );

  const handleApprove = (comment: string) => {
    if (!roundId || !approval) return;
    if (resolveDataSourceMode() === "replay") {
      setApprovalOpen(false);
      setDeck((prev) => prev.filter((x) => x.kind !== "approve"));
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
    </>
  );
}
