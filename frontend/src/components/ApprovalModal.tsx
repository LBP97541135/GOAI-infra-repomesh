import { useEffect, useState } from "react";
import type { ApprovalInfo } from "../types";
import { unverifiedMarkerLabel } from "../display";
import { AgentVerificationBlock } from "./AgentVerificationBlock";
import { Modal } from "./Modal";

/** 快照绑定授权单：授权主体 / 不可变快照 / 30 分钟有效期 / 写入范围 /
 *  「任一 SHA、契约或门禁变化即失效」确认框。治理故事的核心展示。
 *  head-bound 语义由后端保证（契约 §4.4：SHA 漂移即 409）——409 时失效提示
 *  显示在弹窗内，不静默失败。 */

/** 授权主体的解析状态（派生自花名册，见 api/decisions.ts）。
 *  `missing` 必须**禁用提交**：`decided_by_agent_id` 查无此人时后端一定拒绝，
 *  让用户点一个注定失败的按钮，等于把配置问题伪装成审批失败。 */
export interface ApprovalPrincipal {
  state: "resolving" | "ready" | "missing" | "replay";
  label: string;
}

const DEFAULT_COMMENT = "门禁全绿且独立 Review 通过，允许按冻结 SHA 合并。";
/** A-18：有未验证声明时的预填。只陈述门禁这一件已知为真的事，并把「agent 自述未验证」
 *  记进案卷；「是否仍然合并」由审批人自己补，界面不替他写理由。 */
const UNVERIFIED_COMMENT =
  "门禁检查通过；已阅 agent 自述「未验证」声明（见授权单）。合并理由：";

export function ApprovalModal({
  open,
  info,
  submitting,
  errorText,
  principal,
  onCancel,
  onApprove,
}: {
  open: boolean;
  info: ApprovalInfo | null;
  submitting: boolean;
  errorText: string | null;
  principal: ApprovalPrincipal;
  onCancel: () => void;
  onApprove: (comment: string) => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [comment, setComment] = useState(DEFAULT_COMMENT);

  const unverified = info?.unverified ?? [];
  const unverifiedCount = unverified.length;

  // 每次打开都重置确认框——既有语义：授权确认必须每次重新勾选
  useEffect(() => {
    if (open) {
      setConfirmed(false);
      // A-18：审批意见会进审计记录，预填的话不能替审批人断言一件屏幕上正被否认的事。
      // 有未验证声明时，原来那句「门禁全绿且独立 Review 通过」就成了签在案卷上的谎；
      // 换成一句只说门禁、不说验证的，剩下的留给人自己写。
      setComment(unverifiedCount > 0 ? UNVERIFIED_COMMENT : DEFAULT_COMMENT);
    }
  }, [open, unverifiedCount]);

  // 授权主体不再取 viewmodel 的占位串「治理审批人」——它现在有真实来源了
  // （花名册里该组织的 organization_leader），授权单上写谁批的就必须是谁
  const summary: Array<[string, string]> = [
    ["授权主体", principal.label],
    ["绑定快照", info?.snapshotLabel ?? "—"],
    ["有效时间", "30 分钟"],
    ["写入范围", info?.scopeLabel ?? "—"],
  ];

  const blocked = principal.state === "missing" || principal.state === "resolving";

  // C-1（验收缺陷升格修复）语义由共享外壳 Modal 承担：关闭时条件渲染为 null，
  // 不留隐藏 DOM；Esc/backdrop 统一回调 onCancel。
  return (
    <Modal
      open={open}
      onClose={onCancel}
      className="m-auto w-[min(480px,92vw)] rounded-[3px] border border-[#4a4128] bg-panel p-0 text-tx shadow-[0_24px_70px_rgba(0,0,0,0.7)]"
    >
      <div className="flex items-start justify-between border-b border-line px-[22px] pt-5 pb-3.5">
        <div>
          <span className="eyebrow">DELIVERY GATE</span>
          <h2 className="mt-1 text-[16px] font-semibold text-cream">批准临时远程写入</h2>
        </div>
        <button className="text-[18px] text-tx2" aria-label="关闭" onClick={onCancel}>
          ×
        </button>
      </div>

      <div className="px-[22px] py-4">
        <div className="mb-3.5 grid grid-cols-2 gap-2">
          {summary.map(([k, v]) => (
            <div key={k} className="rounded-hard border border-line bg-panel-2 px-[11px] py-2">
              <span className="block font-mono text-[9.5px] tracking-[0.1em] text-tx2">{k}</span>
              <b className="text-[12px] text-cream">{v}</b>
            </div>
          ))}
        </div>

        {info?.headSha && (
          <div className="mb-3 rounded-hard border border-dashed border-line bg-panel-2 px-[11px] py-2">
            <span className="block font-mono text-[9.5px] tracking-[0.1em] text-tx2">HEAD-BOUND SHA</span>
            <b className="font-mono text-[12px] text-cream">{info.headSha.slice(0, 12)}</b>
          </div>
        )}

        {principal.state === "missing" && (
          <div className="mb-3 border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] text-[#e8a184]">
            <b className="mr-1.5 font-mono tracking-[0.08em]">决策主体未接入</b>
            花名册里没有该组织可用的 organization_leader，也没有配置
            VITE_GOVERNANCE_AGENT_ID 覆盖。提交已禁用——治理决策必须记在一个真实主体名下。
          </div>
        )}

        {errorText && (
          <div className="mb-3 border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] text-[#e8a184]">
            <b className="mr-1.5 font-mono tracking-[0.08em]">授权失效</b>
            {errorText}
          </div>
        )}

        {/* A-18：摆在确认框与「批准并授权」之前，因为合并是整条链第一个不可逆动作，
            而 delivery_auto 打开时 CI 一绿就自动合——没有第二个人再看一眼的机会。

            **不拦动作**：按钮该亮还亮，门禁语义是另一轮裁决。这里做的只有一件事——
            不让人在没看见这段话的情况下按下去。措辞也一字不改：agent 说
            "Please re-run before merging."，界面就照抄，不翻译成「建议复核」。 */}
        {unverified.length > 0 && (
          <div className="mb-3 rounded-hard border border-amber bg-[#2a2110] px-3 py-2.5">
            <div className="flex flex-wrap items-baseline gap-x-2 text-[12px] text-amber">
              <b className="font-mono tracking-[0.08em]">
                {unverifiedMarkerLabel(unverified.reduce((n, r) => n + r.blockers.length, 0))}
              </b>
              <span className="text-tx2">
                本次要合并的 {unverified.length} 条任务里，agent 自己说没有可核验的执行记录。
              </span>
            </div>
            <div className="mt-2 grid gap-2">
              {unverified.map((report) => (
                <AgentVerificationBlock key={report.taskId} report={report} />
              ))}
            </div>
            <p className="mt-2 text-[10.5px] leading-[1.6] text-tx3">
              以上为 agent 原话（契约 v0.1 §5.4 `tasks[].evidence`，读模型逐字转述）。
              CI 绿与「agent 验证过自己」是两件事，本提示<b>不改变门禁结论</b>，也不阻止批准。
            </p>
          </div>
        )}

        <label className="block">
          <span className="mb-[5px] block text-[12px] font-semibold text-cream">审批意见</span>
          <textarea
            rows={3}
            className="w-full resize-none rounded-hard border border-line bg-panel-2 px-2.5 py-2 font-sans text-[12.5px] text-tx"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
        </label>

        <label className="mt-3 flex items-start gap-2 text-[12px] text-tx2">
          <input
            type="checkbox"
            className="mt-0.5 accent-amber"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          <span>
            我确认本次授权仅绑定 {info?.snapshotLabel ?? "当前快照"}；任一 SHA、契约或门禁结果变化都会使授权立即失效。
          </span>
        </label>
      </div>

      <div className="flex justify-end gap-2.5 px-[22px] pt-3.5 pb-[18px]">
        <button
          className="rounded-hard border border-line bg-transparent px-3.5 py-2 text-[12.5px] text-tx"
          onClick={onCancel}
        >
          拒绝 / 稍后处理
        </button>
        <button
          className="rounded-hard bg-amber px-4 py-2 text-[12.5px] font-extrabold text-[#191308] hover:bg-amber-hi disabled:cursor-not-allowed disabled:opacity-40"
          disabled={!confirmed || submitting || blocked}
          onClick={() => onApprove(comment)}
        >
          {submitting ? "提交中…" : principal.state === "resolving" ? "解析主体…" : "批准并授权"}
        </button>
      </div>
    </Modal>
  );
}
