import { useEffect, useRef, useState } from "react";

/** 新建 issue 弹窗（CONS-41）：处理者芯片 / 需求文本 / 范围提议 / Ctrl+Enter。
 *  按原型 redesign-issue-centric.html。
 *
 *  ⚠ 写路径缺口：创建 issue = 建 Project + Intake 立项，当前 8100 无对应端点
 *  （/auth/* 与读模型端点之外无 project 写 API）。本弹窗因此**不提交假请求**，
 *  确认时说明缺口并保留输入，等后端写端点（CONS-31 之后）落地再接。 */

export function NewIssueModal({
  open,
  workspaceLabel,
  onClose,
  onToast,
}: {
  open: boolean;
  workspaceLabel: string;
  onClose: () => void;
  onToast: (text: string) => void;
}) {
  const [text, setText] = useState("");
  const areaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (open) areaRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const submit = () => {
    if (!text.trim()) {
      onToast("请先描述要交付什么");
      return;
    }
    // 诚实数据：无写端点，不伪造「已创建」
    onToast("issue 写端点尚未接入（建 Project + Intake 立项），需求文本已保留");
  };

  return (
    <div
      className="fixed inset-0 z-30 grid place-items-center bg-[rgba(10,8,4,0.72)]"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-[560px] max-w-[92vw] rounded-hard border border-line bg-[#1c1710] shadow-[0_24px_60px_rgba(0,0,0,0.6)]">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <span className="text-[12px] text-tx2">{workspaceLabel}</span>
          <span className="text-tx2">›</span>
          <span className="text-[12.5px] text-tx">新建 issue</span>
          <button className="ml-auto text-[14px] text-tx2 hover:text-amber-hi" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 px-4 pt-2.5">
          <span className="text-[11.5px] text-tx2">处理者</span>
          <span className="rounded-hard border border-line px-2 py-px font-mono text-[11px] text-kraft">
            <i className="not-italic text-tx2">●</i> 组织 Leader（未接入）
          </span>
          <span className="text-[10.5px] text-[#6b6046]">Org Leader 负责需求接收与范围提议</span>
        </div>

        <textarea
          ref={areaRef}
          className="min-h-[150px] w-full resize-none bg-transparent px-4 py-3.5 font-sans text-[13px] leading-[1.7] text-tx placeholder:text-[#6b6046] focus:outline-none"
          placeholder='告诉组织要交付什么，例如："在订单结账时记录价格被修改的原因，原因随订单落库并在后台订单详情页展示"'
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              submit();
            }
          }}
        />

        <div className="flex items-center gap-2.5 border-t border-line px-3.5 py-2.5">
          <button
            className="rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-tx2"
            onClick={() => onToast("范围默认由 Org Leader 从仓库摘要提议；手动圈选待仓库读模型（CONS-32）")}
          >
            ▣ 范围 · Org Leader 提议
          </button>
          <button
            className="text-[12px] text-tx2 hover:text-amber-hi"
            onClick={() => onToast("附件（PRD 文档等）为二期能力")}
          >
            📎
          </button>
          <button
            className="ml-auto rounded-hard bg-amber px-4 py-[7px] text-[12.5px] font-extrabold text-[#191308] hover:bg-amber-hi"
            onClick={submit}
          >
            创建 (Ctrl+Enter)
          </button>
        </div>
      </div>
    </div>
  );
}
