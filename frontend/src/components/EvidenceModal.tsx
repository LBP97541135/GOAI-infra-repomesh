import type { EvidenceView } from "../types";
import { eventTime } from "../viewmodel";

/** 证据面弹窗（验收缺陷 B-3 最小版）。
 *
 *  范围：决策指向仓库在本轮的**既有聚合证据**——候选（head/base/分支/PR）、CI 检查、
 *  评审、merge gate、治理决策、变更提交与文件、验证快照。CI 原始报告与 diff 正文
 *  需要新的后端证据端点（推送后立项），本面如实标注缺口而不是装满。
 *
 *  红线：不做状态映射——passed/state/gate 原样透传；merge_gate 为 null 表示
 *  合并请求已发出或已过（§6.4），不等于「不允许」。 */

export function EvidenceModal({
  open,
  roundLabel,
  evidence,
  onClose,
}: {
  open: boolean;
  roundLabel: string;
  evidence: EvidenceView | null;
  onClose: () => void;
}) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-30 grid place-items-center bg-[rgba(10,8,4,0.72)]"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="max-h-[86vh] w-[620px] max-w-[94vw] overflow-y-auto rounded-hard border border-line bg-[#1c1710] shadow-[0_24px_60px_rgba(0,0,0,0.6)]">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <span className="eyebrow text-amber">证据面</span>
          <span className="text-[12px] text-tx2">{roundLabel}</span>
          {evidence && <span className="font-mono text-[12px] text-tx">{evidence.repositoryName}</span>}
          <button className="ml-auto text-[14px] text-tx2 hover:text-amber-hi" onClick={onClose}>
            ✕
          </button>
        </div>

        {!evidence && (
          <p className="px-4 py-6 text-[12.5px] text-tx2">
            本轮聚合里没有该仓库的 ChangeSet 记录——候选尚未发布，暂无可展示证据。
          </p>
        )}

        {evidence && (
          <div className="grid gap-3 px-4 py-3.5">
            <section>
              <div className="microlabel pb-1.5">候选</div>
              <div className="grid gap-1 font-mono text-[11.5px] text-tx2">
                <span>
                  HEAD <b className="text-tx">{evidence.headSha.slice(0, 12)}</b> · BASE{" "}
                  {evidence.baseSha.slice(0, 12)}
                </span>
                <span>分支 {evidence.branchName}</span>
                <span>
                  PR{" "}
                  {evidence.prUrl ? (
                    <a className="text-amber hover:text-amber-hi" href={evidence.prUrl} target="_blank" rel="noreferrer">
                      {evidence.prLabel}
                    </a>
                  ) : (
                    "未创建"
                  )}
                </span>
              </div>
            </section>

            <section>
              <div className="microlabel pb-1.5">CI 检查</div>
              {evidence.ciChecks.length === 0 && <p className="text-[11.5px] text-[#6b6046]">暂无检查记录。</p>}
              {evidence.ciChecks.map((c) => (
                <div key={c.name} className="flex items-baseline gap-2 py-0.5 text-[11.5px]">
                  <span className={`font-mono ${c.passed ? "text-olive" : "text-salmon"}`}>
                    {c.passed ? "✓" : "✗"} {c.name}
                  </span>
                  {c.required && <span className="rounded-hard border border-line px-1 font-mono text-[9.5px] text-tx2">required</span>}
                  <span className="min-w-0 flex-1 truncate text-tx2">{c.summary}</span>
                </div>
              ))}
              <p className="pt-0.5 text-[10.5px] text-[#6b6046]">CI 原始报告未接入（证据端点待立项），此处为门禁观测摘要。</p>
            </section>

            <section>
              <div className="microlabel pb-1.5">评审 · 治理</div>
              {evidence.reviews.length === 0 && (
                <p className="text-[11.5px] text-[#6b6046]">
                  暂无 SCM 评审记录（要求 {evidence.requiredApprovals} 个批准）。
                </p>
              )}
              {evidence.reviews.map((r, i) => (
                <div key={i} className="py-0.5 text-[11.5px] text-tx2">
                  <span className="font-mono text-tx">{r.reviewer}</span> · {r.state} · {r.summary}
                </div>
              ))}
              {evidence.governance.map((g, i) => (
                <div key={i} className="flex flex-wrap items-baseline gap-x-2 py-0.5 text-[11.5px]">
                  <span
                    className={`rounded-hard border px-1.5 font-mono text-[10px] ${
                      g.decision === "ready" ? "border-olive text-olive" : "border-salmon text-salmon"
                    }`}
                  >
                    {g.decision.toUpperCase()}
                  </span>
                  <span className="font-mono text-[10.5px] text-tx2">head {g.headSha.slice(0, 12)}</span>
                  <span className="text-[10.5px] text-[#6b6046]">{eventTime(g.decidedAt)}</span>
                  <span className="w-full text-tx2">{g.reason}</span>
                </div>
              ))}
              {evidence.mergeGate !== null ? (
                <p className={`pt-0.5 text-[11.5px] ${evidence.mergeGate.allowed ? "text-olive" : "text-salmon"}`}>
                  merge gate：{evidence.mergeGate.allowed ? "放行" : `受阻 · ${evidence.mergeGate.reasons.join("；")}`}
                </p>
              ) : (
                <p className="pt-0.5 text-[10.5px] text-[#6b6046]">merge gate 已过评估窗口（合并请求已发出或已完成，§6.4）。</p>
              )}
            </section>

            <section>
              <div className="microlabel pb-1.5">变更</div>
              {evidence.commits.length === 0 && <p className="text-[11.5px] text-[#6b6046]">尚无变更提交。</p>}
              {evidence.commits.map((c) => (
                <div key={c.sha} className="py-0.5">
                  <span className="font-mono text-[11.5px] text-tx">commit {c.sha}</span>
                  <div className="pl-3">
                    {/* diffstat 无源（v0.1 §6.3 恒 null）：只列文件名，不编 ± 行数 */}
                    {c.files.map((f) => (
                      <div key={f} className="font-mono text-[11px] text-tx2">
                        {f}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </section>

            <section>
              <div className="microlabel pb-1.5">验证快照</div>
              {evidence.snapshot ? (
                <div className="font-mono text-[11.5px] text-tx2">
                  {evidence.snapshot.id.slice(0, 8)} · {evidence.snapshot.status} · env{" "}
                  {evidence.snapshot.environmentHash.slice(0, 12)} · 有效至 {eventTime(evidence.snapshot.expiresAt)}
                </div>
              ) : (
                <p className="text-[11.5px] text-[#6b6046]">本轮无验证快照（未接入或未生成）。</p>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
