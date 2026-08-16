import type { EvidenceView } from "../types";
import { eventTime, governanceLabel, governanceSkin, shortId } from "../display";
import { AgentVerificationBlock } from "./AgentVerificationBlock";
import { Modal } from "./Modal";

/** 证据面弹窗（验收缺陷 B-3 最小版）。
 *
 *  范围：决策指向仓库在本轮的**既有聚合证据**——候选（head/base/分支/PR）、CI 检查、
 *  评审、merge gate、治理决策、变更提交与文件、验证快照。CI 原始报告与 diff 正文
 *  需要新的后端证据端点（推送后立项），本面如实标注缺口而不是装满。
 *
 *  红线：不做状态映射——passed/state/gate 原样透传；merge_gate 为 null 表示
 *  合并请求已发出或已过（§6.4），不等于「不允许」。
 *
 *  外壳走共享 Modal（X1）：由此获得 Esc 关闭与焦点陷阱（裁决要求的行为补齐）。 */

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
  return (
    <Modal
      open={open}
      onClose={onClose}
      className="m-auto max-h-[86vh] w-[620px] max-w-[94vw] overflow-y-auto rounded-hard border border-line bg-[#1c1710] p-0 text-tx shadow-[0_24px_60px_rgba(0,0,0,0.6)]"
    >
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
          {/* A-18：放在最前，在 CI 与门禁之前。
              下面几节全是**系统观测到的**（CI 结果、评审、门禁），这一节是**执行者自己
              说的**。系统看到「CI 绿」和 agent 说「我一行都没跑」可以同时为真——live 那条
              就是——所以两者不能互相顶替，也不该让人翻到最后才看到后者。 */}
          <section>
            <div className="microlabel pb-1.5">AGENT 自述验证状态</div>
            {evidence.agentReports.length === 0 ? (
              <p className="text-[11.5px] text-tx3">
                本仓任务没有结构化的 Runner 证据（`tasks[].evidence` 为 null）——没有做过任何
                自述，这与「声明了未验证」不是一回事，故此处留白而不是标记未验证。
              </p>
            ) : (
              <div className="grid gap-2">
                {evidence.agentReports.map((report) => (
                  <AgentVerificationBlock key={report.taskId} report={report} />
                ))}
              </div>
            )}
          </section>

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
            {evidence.ciChecks.length === 0 && <p className="text-[11.5px] text-tx3">暂无检查记录。</p>}
            {evidence.ciChecks.map((c) => (
              <div key={c.name} className="flex items-baseline gap-2 py-0.5 text-[11.5px]">
                <span className={`font-mono ${c.passed ? "text-olive" : "text-salmon"}`}>
                  {c.passed ? "✓" : "✗"} {c.name}
                </span>
                {c.required && <span className="rounded-hard border border-line px-1 font-mono text-[9.5px] text-tx2">required</span>}
                <span className="min-w-0 flex-1 truncate text-tx2">{c.summary}</span>
              </div>
            ))}
            <p className="pt-0.5 text-[10.5px] text-tx3">CI 原始报告未接入（证据端点待立项），此处为门禁观测摘要。</p>
          </section>

          <section>
            <div className="microlabel pb-1.5">评审 · 治理</div>
            {evidence.reviews.length === 0 && (
              <p className="text-[11.5px] text-tx3">
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
                  className={`rounded-hard border px-1.5 font-mono text-[10px] ${governanceSkin(g.decision)}`}
                >
                  {governanceLabel(g.decision)}
                </span>
                <span className="font-mono text-[10.5px] text-tx2">head {g.headSha.slice(0, 12)}</span>
                <span className="text-[10.5px] text-tx3">{eventTime(g.decidedAt)}</span>
                <span className="w-full text-tx2">{g.reason}</span>
              </div>
            ))}
            {evidence.mergeGate !== null ? (
              <p className={`pt-0.5 text-[11.5px] ${evidence.mergeGate.allowed ? "text-olive" : "text-salmon"}`}>
                merge gate：{evidence.mergeGate.allowed ? "放行" : `受阻 · ${evidence.mergeGate.reasons.join("；")}`}
              </p>
            ) : (
              <p className="pt-0.5 text-[10.5px] text-tx3">merge gate 已过评估窗口（合并请求已发出或已完成，§6.4）。</p>
            )}
          </section>

          <section>
            <div className="microlabel pb-1.5">变更</div>
            {evidence.commits.length === 0 && <p className="text-[11.5px] text-tx3">尚无变更提交。</p>}
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
                {shortId(evidence.snapshot.id)} · {evidence.snapshot.status} · env{" "}
                {evidence.snapshot.environmentHash.slice(0, 12)} · 有效至 {eventTime(evidence.snapshot.expiresAt)}
              </div>
            ) : (
              <p className="text-[11.5px] text-tx3">本轮无验证快照（未接入或未生成）。</p>
            )}
          </section>
        </div>
      )}
    </Modal>
  );
}
