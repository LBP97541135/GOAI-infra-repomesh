import type { DeliveryEventItem, DeliveryEventKind } from "../api/contract";
import { eventTime } from "../display";

/** 环境窗「事件时间线」段（CONS-14，契约 §4.1）。kind/text 直投影不做映射。
 *  deny 契约上 live 恒不产出（§6.6）：live 收到即渲染契约违约警示（不静默吞掉）；
 *  回放夹具的 deny 属治理拦截演示叙事，正常渲染。 */

export interface EventsTimelineState {
  items: DeliveryEventItem[];
  nextCursor: string | null;
  kind: DeliveryEventKind | null;
  loading: boolean;
}

/** kind 微标签配色：全部取自 Variant D 既有令牌（index.css），不引入新色值。
 *  bluegray 定稿限定「仅执行中」，此处避用。 */
const KIND_STYLE: Record<DeliveryEventKind, string> = {
  runner: "text-amber", // 琥珀：Runner 执行主体动作
  matrix: "text-kraft", // 牛皮纸：协作通信投递
  gate: "text-olive", // 橄榄：门禁观测（CI/review/merge）
  plan: "text-cream", // 奶油纸：计划版本变化
  deny: "text-salmon", // 赭红：治理拦截（仅回放演示叙事）
};

const FILTERS: Array<{ kind: DeliveryEventKind | null; label: string }> = [
  { kind: null, label: "全部" },
  { kind: "runner", label: "RUNNER" },
  { kind: "matrix", label: "MATRIX" },
  { kind: "gate", label: "GATE" },
  { kind: "plan", label: "PLAN" },
];

export function EventTimeline({
  state,
  demo,
  onFilter,
  onLoadMore,
}: {
  state: EventsTimelineState;
  /** 回放模式（deny 为演示叙事）；live 模式 deny 渲染为契约违约警示 */
  demo: boolean;
  onFilter: (kind: DeliveryEventKind | null) => void;
  onLoadMore: () => void;
}) {
  return (
    <div>
      <div className="flex flex-wrap gap-1 px-2.5 pb-1.5">
        {FILTERS.map((f) => (
          <button
            key={f.label}
            className={`rounded-hard border px-1.5 py-px font-mono text-[9.5px] font-bold tracking-[0.12em] ${
              state.kind === f.kind
                ? "border-amber bg-amber/10 text-amber-hi"
                : "border-line text-tx2 hover:border-amber/60 hover:text-tx"
            }`}
            onClick={() => onFilter(f.kind)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {state.items.length === 0 && !state.loading && (
        <div className="px-2.5 py-[3px] text-[11px] text-tx3">
          {state.kind ? "该类别暂无事件" : "暂无事件"}
        </div>
      )}

      {state.items.map((e, i) => {
        const violation = e.kind === "deny" && !demo;
        return (
          <div
            key={`${e.at}-${e.kind}-${i}`}
            className={`flex items-baseline gap-2 px-2.5 py-[3px] text-[11.5px] ${violation ? "bg-salmon/10" : ""}`}
          >
            <time className="flex-none font-mono text-[10.5px] text-tx2">{eventTime(e.at)}</time>
            <span className={`flex-none font-mono text-[9.5px] font-bold tracking-[0.12em] uppercase ${KIND_STYLE[e.kind]}`}>
              {e.kind}
            </span>
            <span className={`min-w-0 ${violation ? "text-salmon" : "text-tx"}`}>
              {violation ? `⚠ 契约违约条目（deny 不应由后端产出）：${e.text}` : e.text}
            </span>
          </div>
        );
      })}

      {state.loading && <div className="px-2.5 py-[3px] text-[11px] text-tx2">加载中…</div>}

      {state.nextCursor !== null && !state.loading && (
        <button
          className="mx-2.5 mt-1 mb-0.5 rounded-hard border border-line px-2 py-[3px] text-[11px] text-tx2 hover:border-amber hover:text-amber-hi"
          onClick={onLoadMore}
        >
          ↓ 加载后续
        </button>
      )}
    </div>
  );
}
