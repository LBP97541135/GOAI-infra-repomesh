import type { DeliveryListItem, DeliveryListResponse, Phase } from "../api/contract";

/** 左栏项目树：按 project 分组的交付列表（契约 §2）。
 *  phase 由读模型给出，前端只渲染徽标；delivery_id=null 为虚拟草稿交付（§0）。 */

const PHASE_BADGE: Record<Phase, { label: string; dot: string; blink?: boolean }> = {
  contract: { label: "契约", dot: "bg-tx2" },
  plan: { label: "计划", dot: "bg-tx2" },
  execute: { label: "执行中", dot: "bg-bluegray", blink: true },
  validate: { label: "验证中", dot: "bg-amber", blink: true },
  release: { label: "发布门禁", dot: "bg-bluegray", blink: true },
  delivered: { label: "已发布", dot: "bg-olive" },
  failed: { label: "失败", dot: "bg-salmon" },
  archived: { label: "已归档", dot: "bg-tx2" },
};

const ACTIVE_PHASES: Phase[] = ["contract", "plan", "execute", "validate", "release"];

function DeliveryRow({
  item,
  active,
  pendingCount,
  onToast,
}: {
  item: DeliveryListItem;
  active: boolean;
  pendingCount: number | null;
  onToast: (text: string) => void;
}) {
  const badge = PHASE_BADGE[item.phase];
  const pending = pendingCount ?? item.pending_decision_count;
  const sub = [badge.label, item.phase_note, pending > 0 ? `${pending} 项待决策` : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <button
      className={
        active
          ? "flex w-full items-start gap-2 border-l-2 border-amber bg-amber/10 px-2.5 py-2 text-left"
          : "flex w-full items-start gap-2 border-l-2 border-transparent px-2.5 py-2 text-left hover:bg-amber/5"
      }
      onClick={() => {
        if (!active) onToast("MVP：切换交付尚未接入");
      }}
    >
      <span className={`mt-[7px] size-[7px] flex-none ${badge.dot} ${badge.blink ? "blink" : ""}`} />
      <div className="min-w-0">
        <b className={`block truncate text-[13px] font-semibold ${active ? "text-amber-hi" : "text-tx"}`}>
          {item.title}
        </b>
        <small className="text-[11px] text-tx2">{sub}</small>
      </div>
    </button>
  );
}

export function Sidebar({
  list,
  activeDeliveryId,
  pendingCount,
  onToast,
}: {
  list: DeliveryListResponse;
  activeDeliveryId: string | null;
  pendingCount: number;
  onToast: (text: string) => void;
}) {
  const project = list.projects[0] ?? null;
  const deliveries = project?.deliveries ?? [];
  const running = deliveries.filter((d) => ACTIVE_PHASES.includes(d.phase));
  const others = deliveries.filter((d) => !ACTIVE_PHASES.includes(d.phase));

  return (
    <aside className="flex w-[236px] flex-none flex-col border-r border-line bg-ink-deep px-3 pt-4 pb-3">
      <div className="flex items-center gap-2.5 px-1.5 pb-4">
        <span className="grid size-[34px] flex-none place-items-center rounded-hard bg-amber font-mono text-[15px] font-extrabold text-[#16120a]">
          R
        </span>
        <div className="min-w-0">
          <strong className="block font-mono text-[13px] tracking-[0.12em] text-cream">REPOMESH</strong>
          <small className="block truncate font-mono text-[9.5px] tracking-[0.14em] text-tx2 uppercase">
            {project ? project.title : "无项目"}
          </small>
        </div>
      </div>

      <button
        className="flex w-full items-center gap-2 rounded-hard border border-line bg-transparent px-3 py-2 text-[13px] text-tx hover:border-amber hover:text-amber-hi"
        onClick={() => onToast("MVP：新建交付流程尚未接入")}
      >
        ＋ 新建交付
        <kbd className="ml-auto rounded-hard border border-line px-1 py-px font-mono text-[10px] text-tx2">⌘K</kbd>
      </button>

      {running.length > 0 && (
        <>
          <div className="microlabel mx-2 mt-4 mb-1.5">进行中</div>
          {running.map((d) => (
            <DeliveryRow
              key={d.delivery_id ?? `draft-${d.title}`}
              item={d}
              active={d.delivery_id !== null && d.delivery_id === activeDeliveryId}
              pendingCount={d.delivery_id !== null && d.delivery_id === activeDeliveryId ? pendingCount : null}
              onToast={onToast}
            />
          ))}
        </>
      )}

      {others.length > 0 && (
        <>
          <div className="microlabel mx-2 mt-4 mb-1.5">其他交付</div>
          {others.map((d) => (
            <DeliveryRow
              key={d.delivery_id ?? `draft-${d.title}`}
              item={d}
              active={false}
              pendingCount={null}
              onToast={onToast}
            />
          ))}
        </>
      )}

      {deliveries.length === 0 && (
        <div className="mx-2 mt-4 text-[12px] text-tx2">暂无交付数据</div>
      )}

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
