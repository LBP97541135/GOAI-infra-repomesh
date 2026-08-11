import { useEffect, useState } from "react";
import {
  AlertCircle,
  Ban,
  Check,
  CheckCircle2,
  Clock3,
  Code2,
  GitPullRequest,
  Pause,
  Play,
  RotateCcw,
  X,
} from "lucide-react";
import { api, type ReviewRequest } from "./api";

const labels: Record<string, string> = {
  repository_scope: "仓库范围",
  specification: "工程 Spec",
  execution: "开始执行",
  validation: "测试验证",
  delivery: "PR 交付",
  exception_escalation: "异常升级",
};
const icons: Record<string, typeof Code2> = {
  repository_scope: Code2,
  specification: Code2,
  execution: Code2,
  validation: CheckCircle2,
  delivery: GitPullRequest,
  exception_escalation: AlertCircle,
};

export function ReviewWorkbench({
  reviews,
  onRefresh,
}: {
  reviews: ReviewRequest[];
  onRefresh: () => void;
}) {
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const visible =
    filter === "pending"
      ? reviews.filter((item) => item.status === "pending")
      : reviews;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = visible.find((item) => item.id === selectedId) || visible[0];
  useEffect(() => {
    if (visible.length && !visible.some((item) => item.id === selectedId))
      setSelectedId(visible[0].id);
  }, [reviews, filter]);
  return (
    <section className="workbench">
      <div className="queue">
        <div className="queue-head">
          <div>
            <h2>审核队列</h2>
            <p>{visible.length} 项需要关注</p>
          </div>
          <button className="icon-button" title="刷新" onClick={onRefresh}>
            <RotateCcw size={17} />
          </button>
        </div>
        <div className="tabs">
          <button
            className={filter === "pending" ? "active" : ""}
            onClick={() => setFilter("pending")}
          >
            待处理 <b>{reviews.filter((i) => i.status === "pending").length}</b>
          </button>
          <button
            className={filter === "all" ? "active" : ""}
            onClick={() => setFilter("all")}
          >
            全部
          </button>
        </div>
        <div className="queue-list">
          {visible.map((item) => {
            const Icon = icons[item.checkpoint] || AlertCircle;
            return (
              <button
                key={item.id}
                className={selected?.id === item.id ? "selected" : ""}
                onClick={() => setSelectedId(item.id)}
              >
                <span className="queue-icon">
                  <Icon size={17} />
                </span>
                <div>
                  <span>
                    <em>{labels[item.checkpoint] || item.checkpoint}</em>
                    <time>
                      <Clock3 size={12} />
                      刚刚
                    </time>
                  </span>
                  <strong>{item.title}</strong>
                  <small>
                    项目 {item.project_id.slice(0, 8)} · 证据{" "}
                    {item.evidence_version}
                  </small>
                </div>
              </button>
            );
          })}
          {visible.length === 0 && (
            <div className="queue-empty">
              <CheckCircle2 size={25} />
              <strong>没有待处理审核</strong>
              <span>Agent 提交后会自动出现在这里</span>
            </div>
          )}
        </div>
      </div>
      <ReviewDetail review={selected} onDone={onRefresh} />
    </section>
  );
}

function ReviewDetail({
  review,
  onDone,
}: {
  review?: ReviewRequest;
  onDone: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [decisionError, setDecisionError] = useState("");
  const [controlMessage, setControlMessage] = useState("");
  useEffect(() => {
    setReason("");
    setDecisionError("");
  }, [review?.id]);
  if (!review)
    return (
      <div className="review-empty">
        <CheckCircle2 size={35} />
        <h2>审核队列已清空</h2>
        <p>Agent 可以继续执行，不需要人工介入。</p>
      </div>
    );
  const decide = async (decision: string) => {
    setBusy(true);
    setDecisionError("");
    try {
      await api.decide(
        review,
        decision,
        reason || (decision === "approved" ? "确认通过" : "请根据审核意见修改"),
      );
      onDone();
    } catch (error) {
      setDecisionError((error as Error).message);
      onDone();
    } finally {
      setBusy(false);
    }
  };
  const control = async (action: string) => {
    if (
      action === "cancel_project" &&
      !window.confirm("取消后项目不可恢复，确认继续？")
    )
      return;
    try {
      await api.controlProject(review.project_id, action);
      setControlMessage(
        action === "pause_project"
          ? "项目已暂停"
          : action === "resume_project"
            ? "项目已恢复"
            : "项目已取消",
      );
    } catch (error) {
      setControlMessage((error as Error).message);
    }
  };
  return (
    <article className="review-detail">
      <div className="detail-head">
        <div>
          <span className="status-pill">
            <i />
            等待人工确认
          </span>
          <h2>{review.title}</h2>
          <p>{review.summary}</p>
        </div>
        <span className="checkpoint-badge">{labels[review.checkpoint]}</span>
      </div>
      <div className="project-controls">
        <span>项目控制</span>
        <button onClick={() => control("pause_project")}>
          <Pause size={14} />
          暂停
        </button>
        <button onClick={() => control("resume_project")}>
          <Play size={14} />
          恢复
        </button>
        <button className="cancel" onClick={() => control("cancel_project")}>
          <Ban size={14} />
          取消
        </button>
        {controlMessage && <em>{controlMessage}</em>}
      </div>
      <div className="evidence-strip">
        <div>
          <span>审核单</span>
          <code>{review.id}</code>
        </div>
        <div>
          <span>项目</span>
          <code>{review.project_id}</code>
        </div>
        <div>
          <span>仓库范围</span>
          <code>{review.repository_id || "跨仓库 / 全项目"}</code>
        </div>
        <div>
          <span>证据版本</span>
          <code>{review.evidence_version}</code>
        </div>
        <div>
          <span>请求 Agent</span>
          <code>{review.requested_by_agent_id || "系统检查点"}</code>
        </div>
        <div>
          <span>创建时间</span>
          <code>{new Date(review.created_at).toLocaleString("zh-CN")}</code>
        </div>
      </div>
      <div className="agent-trace">
        <h3>为什么需要你确认</h3>
        <div className="trace-row">
          <span className="agent-avatar">A</span>
          <div>
            <strong>Agent 已完成当前阶段</strong>
            <p>
              执行链已在 <b>{labels[review.checkpoint]}</b>{" "}
              检查点暂停，审批前不会继续产生受控写操作。
            </p>
          </div>
          <time>刚刚</time>
        </div>
        <div className="trace-row human">
          <span className="agent-avatar">人</span>
          <div>
            <strong>等待人工决定</strong>
            <p>
              批准后沿用当前证据版本继续；要求修改或拒绝会将意见返回上级 Agent。
            </p>
          </div>
        </div>
      </div>
      <div className="decision-box">
        {decisionError && <div className="error">{decisionError}</div>}
        <label>
          审核意见
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="可选：记录判断依据，要求修改时请写明具体原因"
          />
        </label>
        <div className="decision-actions">
          <button
            className="danger-ghost"
            disabled={busy}
            onClick={() => decide("rejected")}
          >
            <X size={17} />
            拒绝
          </button>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => decide("changes_requested")}
          >
            <RotateCcw size={16} />
            要求修改
          </button>
          <button
            className="primary"
            disabled={busy}
            onClick={() => decide("approved")}
          >
            <Check size={17} />
            批准并继续
          </button>
        </div>
      </div>
    </article>
  );
}
