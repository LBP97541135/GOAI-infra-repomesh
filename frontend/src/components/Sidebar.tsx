import { delivery } from "../data/mock";

export function Sidebar({ onToast }: { onToast: (text: string) => void }) {
  return (
    <aside className="flex w-[236px] flex-none flex-col border-r border-line bg-ink-deep px-3 pt-4 pb-3">
      <div className="flex items-center gap-2.5 px-1.5 pb-4">
        <span className="grid size-[34px] flex-none place-items-center rounded-hard bg-amber font-mono text-[15px] font-extrabold text-[#16120a]">
          R
        </span>
        <div>
          <strong className="block font-mono text-[13px] tracking-[0.12em] text-cream">REPOMESH</strong>
          <small className="font-mono text-[9.5px] tracking-[0.14em] text-tx2">SALEOR COMMERCE</small>
        </div>
      </div>

      <button
        className="flex w-full items-center gap-2 rounded-hard border border-line bg-transparent px-3 py-2 text-[13px] text-tx hover:border-amber hover:text-amber-hi"
        onClick={() => onToast("MVP：新建交付流程尚未接入")}
      >
        ＋ 新建交付
        <kbd className="ml-auto rounded-hard border border-line px-1 py-px font-mono text-[10px] text-tx2">⌘K</kbd>
      </button>

      <div className="microlabel mx-2 mt-4 mb-1.5">进行中</div>
      <button className="flex w-full items-start gap-2 border-l-2 border-amber bg-amber/10 px-2.5 py-2 text-left">
        <span className="mt-[7px] size-[7px] flex-none bg-bluegray blink" />
        <div>
          <b className="block text-[13px] font-semibold text-amber-hi">{delivery.title.slice(0, 13)}…</b>
          <small className="text-[11px] text-tx2">{delivery.id} · 3 项待决策</small>
        </div>
      </button>

      <div className="microlabel mx-2 mt-4 mb-1.5">其他交付</div>
      <button className="flex w-full items-start gap-2 border-l-2 border-transparent px-2.5 py-2 text-left hover:bg-amber/5">
        <span className="mt-[7px] size-[7px] flex-none bg-olive" />
        <div>
          <b className="block text-[13px] font-semibold text-tx">购物车库存提示优化</b>
          <small className="text-[11px] text-tx2">已发布 · 08-06</small>
        </div>
      </button>
      <button className="flex w-full items-start gap-2 border-l-2 border-transparent px-2.5 py-2 text-left hover:bg-amber/5">
        <span className="mt-[7px] size-[7px] flex-none bg-tx2" />
        <div>
          <b className="block text-[13px] font-semibold text-tx">订单导出增加税率列</b>
          <small className="text-[11px] text-tx2">契约澄清中 · 08-10</small>
        </div>
      </button>

      <div className="mt-auto grid gap-2 border-t border-line pt-2.5">
        <div className="flex items-center gap-2 px-2 py-1.5">
          <span className="size-2 flex-none bg-olive shadow-[0_0_0_3px_rgba(148,163,90,0.18)] blink" />
          <div>
            <b className="block font-mono text-[11px] tracking-[0.08em] text-tx">AGENT RUNTIME</b>
            <small className="text-[10.5px] text-tx2">1 Leader · 3 Workers 在线</small>
          </div>
        </div>
        <div className="flex items-center gap-2 px-2 py-1.5">
          <span className="grid size-7 flex-none place-items-center rounded-hard bg-[#4a4130] text-[12px] font-extrabold text-cream">
            王
          </span>
          <div>
            <b className="block text-[12px] text-tx">王倩</b>
            <small className="text-[10.5px] text-tx2">产品经理</small>
          </div>
        </div>
      </div>
    </aside>
  );
}
