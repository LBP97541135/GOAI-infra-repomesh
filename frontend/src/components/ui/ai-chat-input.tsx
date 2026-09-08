import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Paperclip, Send } from "lucide-react";

/** AIChatInput：吸底对话输入框（2026-09-08 集成；形状按用户参考图重画）。
 *
 *  形态（本轮定稿）：大圆角（18px）柔和浮卡——输入区在上，按钮行内嵌在底部：
 *  左侧附件小圆钮，右侧圆形琥珀发送钮（Claude / ZCode 式布局）。
 *  高度 88px 起步、多行自增、封顶 240px 后内部滚动；无投影、边框恒为
 *  line 一档不随聚焦变色（此前两轮用户裁决继续遵守）。
 *
 *  保留的机制：受控值与幂等键在消费方；附件插槽 `attachment` 出现时展示在
 *  输入区上方（容器 overflow-hidden 让分隔线贴住圆角）；Ctrl/⌘+Enter 发送
 *  （Enter 换行）；禁用态整框置灰。 */

export function AIChatInput({
  value,
  onValueChange,
  onSend,
  sending = false,
  disabled = false,
  placeholder,
  onAttach,
  attachDisabled = false,
  attachTitle = "附件",
  sendTitle = "发送（Ctrl+Enter）",
  sendDisabled = false,
  attachment = null,
  className = "",
}: {
  /** 受控文本（幂等键逻辑在消费方 onChange 里） */
  value: string;
  onValueChange: (text: string) => void;
  onSend: () => void;
  /** 发送中：按钮置灰，Ctrl+Enter 也吞掉 */
  sending?: boolean;
  /** 整框禁用（既有会话无追问时） */
  disabled?: boolean;
  /** 静态占位文案 */
  placeholder?: string;
  /** 回形针 = 文档附件入口；不传则隐藏按钮 */
  onAttach?: () => void;
  attachDisabled?: boolean;
  attachTitle?: string;
  sendTitle?: string;
  /** 消费方侧的发送禁用（如空文本且无附件）；组件侧的 disabled/sending 另算 */
  sendDisabled?: boolean;
  /** 附件卡（文件名/移除由消费方渲染），出现时显示在输入区上方 */
  attachment?: ReactNode;
  className?: string;
}) {
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  // 自增高：88px 一块起步，封顶 240px 后内部滚动
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [value]);

  return (
    <div className={`overflow-hidden rounded-[18px] border border-line bg-panel shadow-[var(--chat-shadow)] ${className}`}>
      {/* 附件卡在输入区上方（2026-09-08 用户裁决），分隔线由消费方的 border-t 承担 */}
      {attachment}

      <textarea
        ref={inputRef}
        rows={1}
        value={value}
        disabled={disabled}
        onChange={(e) => onValueChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            if (!disabled && !sendDisabled && !sending) onSend();
          }
        }}
        placeholder={placeholder}
        className="block min-h-[88px] w-full resize-none bg-transparent px-4 pb-1 pt-3.5 font-sans text-[12.5px] leading-[1.6] text-tx outline-none placeholder:text-tx3 disabled:cursor-not-allowed"
      />

      {/* 按钮行内嵌底部：附件居左，发送居右（ZCode 式布局） */}
      <div className="flex items-center gap-1 px-2.5 pb-2">
        {onAttach && (
          <button
            className="grid size-7 flex-none place-items-center rounded-full text-tx3 transition hover:bg-panel-2 hover:text-tx disabled:opacity-40 disabled:hover:bg-transparent"
            title={attachTitle}
            type="button"
            tabIndex={-1}
            disabled={attachDisabled}
            onClick={(e) => {
              e.stopPropagation();
              onAttach();
            }}
          >
            <Paperclip size={14} />
          </button>
        )}

        <button
          className="ml-auto grid size-8 flex-none place-items-center rounded-full bg-amber text-on-amber transition hover:bg-amber-hi disabled:opacity-40"
          title={sendTitle}
          type="button"
          tabIndex={-1}
          disabled={disabled || sending || sendDisabled}
          onClick={onSend}
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
