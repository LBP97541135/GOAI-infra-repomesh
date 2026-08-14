import { useEffect, useState } from "react";
import {
  type CheckpointDecisionKind,
  type HumanReviewRequestView,
  type HumanReviewStatus,
  type ProjectCheckpoint,
  fetchReviewRequests,
  recordCheckpointDecision,
} from "../api/reviewDesk";
import { dayLabel, errText, shortId } from "../display";
import { ErrorPanel, LoadingLine } from "../components/StatusBlocks";

/** 人工审核台（迁移 2：main 的 ReviewWorkbench 进控制台）。
 *
 *  **为什么是独立一页而不是塞进 issue 详情**：这是一条**跨 issue 的待办队列**。
 *  按 issue 拆开就没有队列了——「今天有几件事等我」正是它唯一要回答的问题。
 *  issue 详情页此前那句「人工检查点走 main 既有审核台」指的就是这里，现在它在同一
 *  个控制台内。
 *
 *  **与决策夹（DecisionDeck）不是一回事**：决策夹装的是**治理决策**（交付门禁上的
 *  ready/blocked/rollback），落在某一轮交付上；这里装的是**项目检查点**
 *  （repository_scope / specification / execution / validation / delivery /
 *  exception_escalation），由拓扑的 `required_checkpoints` 定义、卡在流程节点上。
 *  两者的后端存储、决策类型与权限判定都不同，合并呈现会让人以为批了一个就等于批了
 *  另一个。契约 v0.2 §3/Q6 明文写了决策夹不含 ReviewRequest。
 *
 *  **看得见多少取决于你是谁**：管理员看全部，其他账号只看指派给自己的（后端
 *  `_reviews_for`）。所以队列长度因人而异，这是设计不是取数不稳。
 *
 *  **实时性**：SSE（`/review-requests/events`，2s 比对、变了才推）。流断了退回一次性
 *  取数的结果并显示提示，不静默——一个不再更新却看起来正常的待办队列比空白更危险。 */

const CHECKPOINT_LABEL: Record<ProjectCheckpoint, string> = {
  repository_scope: "仓库范围",
  specification: "规格",
  execution: "执行",
  validation: "验证",
  delivery: "交付",
  exception_escalation: "异常升级",
};

const STATUS_LABEL: Record<HumanReviewStatus, string> = {
  pending: "待审",
  approved: "已通过",
  rejected: "已驳回",
  changes_requested: "要求修改",
};

const STATUS_SKIN: Record<HumanReviewStatus, string> = {
  pending: "border-amber text-amber",
  approved: "border-olive text-olive",
  rejected: "border-salmon text-salmon",
  changes_requested: "border-line text-tx2",
};

const DECISIONS: Array<{ kind: CheckpointDecisionKind; label: string }> = [
  { kind: "approved", label: "通过" },
  { kind: "changes_requested", label: "要求修改" },
  { kind: "rejected", label: "驳回" },
];

function ReviewCard({
  review,
  onDecide,
}: {
  review: HumanReviewRequestView;
  onDecide: (review: HumanReviewRequestView, decision: CheckpointDecisionKind, reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<CheckpointDecisionKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = review.status === "pending";

  const decide = async (kind: CheckpointDecisionKind) => {
    setBusy(kind);
    setError(null);
    try {
      await onDecide(review, kind, reason.trim());
      setReason("");
    } catch (err) {
      // 403 没有决策权 / 409 已决或证据漂移——detail 原文，各有各的下一步
      setError(errText(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-hard border border-line bg-panel px-4 py-3">
      <div className="flex flex-wrap items-baseline gap-2.5">
        <span className="text-[12.5px] text-tx">{review.title}</span>
        <span className={`rounded-hard border px-2 py-px text-[11px] ${STATUS_SKIN[review.status]}`}>
          {STATUS_LABEL[review.status]}
        </span>
        <span className="rounded-hard border border-line px-2 py-px text-[11px] text-tx2">
          {CHECKPOINT_LABEL[review.checkpoint] ?? review.checkpoint}
        </span>
        <span className="ml-auto font-mono text-[10.5px] text-tx3" title={`项目 ${review.project_id}`}>
          #{shortId(review.project_id)} · {dayLabel(review.created_at)}
        </span>
      </div>

      {review.summary && (
        <p className="mt-1.5 whitespace-pre-wrap text-[11.5px] leading-[1.7] text-tx2">{review.summary}</p>
      )}

      <div className="mt-1.5 font-mono text-[10.5px] text-tx3">
        {/* 证据版本要露出来：决策钉在它上面，漂了就是 409 */}
        证据 {review.evidence_version}
        {review.repository_id !== null && ` · 仓库 ${shortId(review.repository_id)}`}
        {review.resolved_by_human_id !== null && ` · 决策人 ${shortId(review.resolved_by_human_id)}`}
      </div>

      {pending && (
        <div className="mt-2.5">
          <input
            className="w-full rounded-hard border border-line bg-ink px-2.5 py-1.5 text-[12px] text-tx placeholder:text-tx3 focus:border-amber focus:outline-none"
            placeholder="决策理由（随决策一并记录）"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="mt-2 flex flex-wrap gap-1.5">
            {DECISIONS.map((item) => (
              <button
                key={item.kind}
                className="rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi disabled:opacity-50"
                disabled={busy !== null}
                onClick={() => decide(item.kind)}
              >
                {busy === item.kind ? "提交中…" : item.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {error && (
        <div className="mt-2 rounded-hard border border-salmon/60 bg-salmon/10 px-2.5 py-1.5 text-[11.5px] text-salmon">
          {error}
        </div>
      )}
    </div>
  );
}

export function ReviewDeskPage({
  rows,
  error,
  streaming,
  onRefresh,
  onToast,
}: {
  /** null = 尚未取到（加载中）。空数组 = 队列真的空了，两态不合并。 */
  rows: HumanReviewRequestView[] | null;
  error: string | null;
  /** SSE 是否还活着；false 时列表仍可读，只是不再自动更新 */
  streaming: boolean;
  onRefresh: () => void;
  onToast: (text: string) => void;
}) {
  const [showResolved, setShowResolved] = useState(false);
  const [resolved, setResolved] = useState<HumanReviewRequestView[] | null>(null);
  const [resolvedError, setResolvedError] = useState<string | null>(null);

  // 已决条目按需取：SSE 只推 pending，已决的要另外问一次全量再筛。
  useEffect(() => {
    if (!showResolved) return;
    let cancelled = false;
    setResolvedError(null);
    fetchReviewRequests()
      .then((all) => !cancelled && setResolved(all.filter((r) => r.status !== "pending")))
      .catch((err: unknown) => !cancelled && setResolvedError(errText(err)));
    return () => {
      cancelled = true;
    };
  }, [showResolved]);

  const decide = async (
    review: HumanReviewRequestView,
    decision: CheckpointDecisionKind,
    reason: string,
  ) => {
    await recordCheckpointDecision(review.project_id, {
      review_request_id: review.id,
      decision,
      reason,
    });
    onToast(`已记录决策：${review.title} → ${STATUS_LABEL[decision]}`);
    // SSE 会把这条从待办里撤走；主动刷一次是为了流断时也能收敛。
    onRefresh();
  };

  return (
    <div className="max-w-[860px]">
      <div className="flex items-baseline gap-3 border-b border-line pb-3">
        <h1 className="text-[16px] font-semibold text-cream">人工审核</h1>
        {rows !== null && <span className="text-[11.5px] text-tx2">{rows.length} 项待审</span>}
        <button
          className="ml-auto rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
          onClick={() => setShowResolved((v) => !v)}
        >
          {showResolved ? "只看待审" : "查看已决"}
        </button>
      </div>

      {!streaming && rows !== null && (
        <p className="mt-3 rounded-hard border border-line bg-panel px-3 py-2 text-[11.5px] text-tx2">
          实时流已断开，下面是最后一次取到的结果，不会自动更新。
          <button className="ml-2 text-amber hover:text-amber-hi" onClick={onRefresh}>
            重新取一次
          </button>
        </p>
      )}

      {error ? (
        <ErrorPanel title="审核队列加载失败" message={error} onRetry={onRefresh} />
      ) : rows === null ? (
        <LoadingLine />
      ) : rows.length === 0 ? (
        <div className="py-8 text-center text-[12.5px] text-tx3">
          没有待审事项。检查点由项目拓扑的 required_checkpoints 定义，
          没有受控项目时这里长期为空是正常的。
        </div>
      ) : (
        <div className="mt-4 grid gap-2">
          {rows.map((review) => (
            <ReviewCard key={review.id} review={review} onDecide={decide} />
          ))}
        </div>
      )}

      {showResolved && (
        <div className="mt-6">
          <div className="eyebrow mb-1.5">已决</div>
          {resolvedError ? (
            <p className="text-[11.5px] text-salmon">{resolvedError}</p>
          ) : resolved === null ? (
            <LoadingLine />
          ) : resolved.length === 0 ? (
            <p className="text-[11.5px] text-tx3">还没有已决事项。</p>
          ) : (
            <div className="grid gap-2">
              {resolved.map((review) => (
                <ReviewCard key={review.id} review={review} onDecide={decide} />
              ))}
            </div>
          )}
        </div>
      )}

      <p className="pt-4 text-[11px] text-tx3">
        数据源：live · GET /review-requests（human_control 面，认本地登录会话）。
        决策人取自会话账号，前端不传。管理员看全部，其他账号只看指派给自己的。
      </p>
    </div>
  );
}
