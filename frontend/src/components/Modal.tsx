import { useEffect, useRef, type ReactNode } from "react";

/** 统一弹窗外壳（X1）：三个业务弹窗共用的原生 <dialog> 底座。
 *
 *  - `open === false` 时**条件渲染为 null**——不留隐藏 DOM。这是 C-1 验收缺陷的
 *    修复语义（隐藏的 <dialog> 仍进可访问性树、querySelector 会先咬到它），别退回
 *    showModal()/close() 显隐的老路。
 *  - 打开后经 useEffect 调 showModal()：拿到原生的 Esc 关闭、焦点陷阱与 top layer。
 *  - Esc（cancel→close，按 dialog 语义走 close 事件）与点击 backdrop
 *    （e.target === dialog 自身）都回调 `onClose`；由调用方把 open 置 false 完成卸载。
 *  - 遮罩底色只有一套：index.css 的 dialog::backdrop（rgba(10,8,4,0.6)+blur(2px)），
 *    这里不再造第二层 fixed inset-0 遮罩。
 *  - 宽度/边框/底色等视觉全部由 `className` 透传，外壳不带样式默认值。 */
export function Modal({
  open,
  className,
  onClose,
  children,
}: {
  open: boolean;
  /** 透传到 <dialog> 元素本身（宽度、边框、底色等）。记得带 m-auto 与 p-0。 */
  className?: string;
  /** Esc、backdrop 点击、以及调用方内部的关闭按钮统一走这里。 */
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (!open) return;
    const dlg = ref.current;
    if (dlg && !dlg.open) dlg.showModal();
  }, [open]);

  if (!open) return null;

  return (
    <dialog
      ref={ref}
      className={className}
      // Esc 走原生 cancel→close；只挂 close 一处，避免同一次关闭回调两遍
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      {children}
    </dialog>
  );
}
