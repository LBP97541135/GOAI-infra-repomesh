/** 回放模式控制条（仅 replay 数据源显示）：一键回放完整闭环，可暂停/重置/跳转。 */

export interface SceneMeta {
  key: string;
  label: string;
  note: string;
}

export function ReplayBar({
  scenes,
  current,
  playing,
  onSelect,
  onTogglePlay,
  onReset,
}: {
  scenes: SceneMeta[];
  current: number;
  playing: boolean;
  onSelect: (index: number) => void;
  onTogglePlay: () => void;
  onReset: () => void;
}) {
  return (
    <div className="flex flex-none items-center gap-2 border-b border-line bg-[#1b1610] px-[22px] py-1.5">
      <span className="microlabel flex-none tracking-[0.14em] text-amber">
        REPLAY<i className="ml-1.5 not-italic text-tx2">回放模式</i>
      </span>
      <div className="flex min-w-0 items-center gap-1 overflow-x-auto">
        {scenes.map((s, i) => (
          <button
            key={s.key}
            title={s.note}
            className={
              i === current
                ? "flex-none rounded-hard bg-amber px-2.5 py-[3px] font-mono text-[10.5px] font-extrabold tracking-[0.06em] text-[#191308]"
                : i < current
                  ? "flex-none rounded-hard border border-[#5a4d33] px-2.5 py-[3px] font-mono text-[10.5px] tracking-[0.06em] text-amber-hi hover:border-amber"
                  : "flex-none rounded-hard border border-line px-2.5 py-[3px] font-mono text-[10.5px] tracking-[0.06em] text-tx2 hover:border-amber hover:text-amber-hi"
            }
            onClick={() => onSelect(i)}
          >
            {i + 1} {s.label}
          </button>
        ))}
      </div>
      <span className="ml-auto flex flex-none items-center gap-1">
        <button
          className="rounded-hard border border-line px-2.5 py-[3px] font-mono text-[11px] font-bold text-tx hover:border-amber hover:text-amber-hi"
          title={playing ? "暂停" : "回放完整闭环"}
          onClick={onTogglePlay}
        >
          {playing ? "⏸ 暂停" : "▶ 回放"}
        </button>
        <button
          className="rounded-hard border border-line px-2.5 py-[3px] font-mono text-[11px] text-tx2 hover:border-amber hover:text-amber-hi"
          title="重置到契约冻结"
          onClick={onReset}
        >
          ↺
        </button>
      </span>
    </div>
  );
}
