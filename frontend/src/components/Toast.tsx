import { useEffect, useRef } from "react";

/** Top-layer toast：用 popover 属性进入 top layer，确保始终浮于 showModal() 弹窗之上。 */
export function Toast({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.showPopover();
    return () => {
      try { el.hidePopover(); } catch { /* already hidden */ }
    };
  }, []);

  return (
    <div
      ref={ref}
      popover="manual"
      style={{ position: "fixed", inset: "auto", bottom: 28, left: "50%", transform: "translateX(-50%)", margin: 0 }}
      className="rounded-hard border-none bg-kraft px-4 py-2 text-[12.5px] text-paper-ink"
    >
      {text}
    </div>
  );
}

