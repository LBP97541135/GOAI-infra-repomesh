import { useEffect, useRef, useState } from "react";
import type { ApprovalInfo } from "../types";

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
  const ref = useRef<HTMLDialogElement>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [comment, setComment] = useState("门禁全绿且独立 Review 通过，允许按冻结 SHA 合并。");

  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (open && !dlg.open) {
      setConfirmed(false);
      dlg.showModal();
    } else if (!open && dlg.open) {
      dlg.close();
    }
  }, [open]);

  // 授权主体不再取 viewmodel 的占位串「治理审批人」——它现在有真实来源了
  // （花名册里该组织的 organization_leader），授权单上写谁批的就必须是谁
  const summary: Array<[string, string]> = [
    ["授权主体", principal.label],
    ["绑定快照", info?.snapshotLabel ?? "—"],
    ["有效时间", "30 分钟"],
    ["写入范围", info?.scopeLabel ?? "—"],
  ];

  const blocked = principal.state === "missing" || principal.state === "resolving";

  // C-1（验收缺陷升格修复）：关闭时不渲染。此前 <dialog> 常驻 DOM 靠 showModal()
  // 显隐，隐藏的表单仍出现在可访问性树里，且 querySelector('textarea') 会先选中它
  // （验收实走中实证咬人）。条件渲染的固有后果是每次打开表单状态重置为初始值——
  // 对确认框正是既有语义（原本就每次 open 复位），对意见框是可接受的默认行为。
  if (!open) return null;

  return (
    <dialog
      ref={ref}
      className="m-auto w-[min(480px,92vw)] rounded-[3px] border border-[#4a4128] bg-panel p-0 text-tx shadow-[0_24px_70px_rgba(0,0,0,0.7)]"
      onClose={() => open && onCancel()}
      onCancel={() => open && onCancel()}
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
    </dialog>
  );
}
