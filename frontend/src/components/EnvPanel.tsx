import { useState } from "react";
import type { DeliveryEventKind } from "../api/contract";
import type { DeliveryView, GateState } from "../types";
import { EventTimeline, type EventsTimelineState } from "./EventTimeline";
import { ChevronIcon, DiffIcon, EnvIcon, PlusIcon, PrIcon, RepoIcon, TermIcon } from "./icons";

/** 悬浮环境窗（Codex 式）：关联仓库 diff → ChangeSet → 环境 → 后台进程。
 *  nullable 降级：diffstat 缺失只列文件名（±隐藏）；cost/trace/matrix/快照为 null
 *  时对应行隐藏（契约 §6.3/§6.4/§6.8）。 */

const PR_STATE: Record<GateState, [string, string]> = {
  open: ["待合并", "text-olive"],
  blocked: ["门禁受阻", "text-salmon"],
  running: ["CI 运行中", "text-bluegray"],
  waiting: ["未创建", "text-tx2"],
};

const rowBase = "flex w-full items-center gap-2.5 rounded-hard px-2.5 py-[7px] text-left text-[12.5px] text-tx";

function Label({ children }: { children: React.ReactNode }) {
  return <div className="microlabel px-2.5 pt-3 pb-[5px] tracking-[0.14em]">{children}</div>;
}

function StaticRow({
  icon,
  name,
  nameClass,
  end,
}: {
  icon: React.ReactNode;
  name: string;
  nameClass?: string;
  end?: React.ReactNode;
}) {
  return (
    <div className={rowBase}>
      <span className="grid size-4 flex-none place-items-center text-amber [&_svg]:size-4">{icon}</span>
      <span className={`min-w-0 truncate ${nameClass ?? ""}`}>{name}</span>
      {end && <span className="ml-auto flex flex-none items-center gap-[7px] text-[12px]">{end}</span>}
    </div>
  );
}

export function EnvPanel({
  view,
  events,
  demo,
  onEventsFilter,
  onEventsMore,
  onToast,
}: {
  view: DeliveryView;
  events: EventsTimelineState;
  demo: boolean;
  onEventsFilter: (kind: DeliveryEventKind | null) => void;
  onEventsMore: () => void;
  onToast: (text: string) => void;
}) {
  const [minimized, setMinimized] = useState(false);
  const [openRepos, setOpenRepos] = useState<Set<string>>(() => new Set(view.repoDiffs.slice(0, 1).map((r) => r.id)));

  const toggleRepo = (id: string) =>
    setOpenRepos((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <aside
      className={`absolute top-[68px] right-4 z-[9] flex w-[300px] flex-col rounded border border-[#4a4128] bg-[rgba(26,22,14,0.97)] shadow-[0_16px_48px_rgba(0,0,0,0.6)] backdrop-blur-[4px] ${minimized ? "" : "max-h-[calc(100%-180px)]"}`}
    >
      <div className="flex flex-none items-center justify-between px-3.5 pt-3 pb-2">
        <span className="font-mono text-[12px] font-bold tracking-[0.12em] text-cream">环境信息</span>
        <span className="flex items-center gap-1">
          <button
            className="rounded-hard p-1 text-tx2 hover:bg-amber/10 hover:text-amber-hi"
            onClick={() => onToast("MVP：挂载补充上下文尚未接入")}
          >
            <PlusIcon className="size-4" />
          </button>
          <button
            className="rounded-hard p-1 text-tx2 hover:bg-amber/10 hover:text-amber-hi"
            title="收起/展开"
            onClick={() => setMinimized((v) => !v)}
          >
            <ChevronIcon className={`size-4 ${minimized ? "rotate-180" : ""}`} />
          </button>
        </span>
      </div>

      {!minimized && (
        <div className="flex-1 overflow-y-auto px-2 pt-0.5 pb-3.5">
          <Label>关联仓库</Label>
          {view.repoDiffs.map((r) => {
            const open = openRepos.has(r.id);
            const hasTotals = r.add !== undefined && r.del !== undefined && r.add + r.del > 0;
            return (
              <div key={r.id}>
                <button className={`${rowBase} hover:bg-amber/5`} onClick={() => toggleRepo(r.id)}>
                  <span className="grid size-4 flex-none place-items-center text-amber [&_svg]:size-4">
                    <RepoIcon />
                  </span>
                  <span className="min-w-0 truncate font-mono">{r.id}</span>
                  <span className="ml-auto flex flex-none items-center gap-[7px] text-[12px]">
                    {hasTotals ? (
                      <>
                        <i className="font-mono text-[11.5px] not-italic text-olive">+{r.add}</i>
                        <i className="font-mono text-[11.5px] not-italic text-salmon">-{r.del}</i>
                      </>
                    ) : r.files.length > 0 ? (
                      <i className="font-mono text-[11px] not-italic text-tx2">{r.files.length} 文件</i>
                    ) : (
                      <i className="text-[11px] not-italic text-tx2">—</i>
                    )}
                    <ChevronIcon className={`size-4 text-tx2 transition-transform ${open ? "rotate-180" : ""}`} />
                  </span>
                </button>
                {open && (
                  <div>
                    <div className="mb-1 ml-6 border-l border-[#453c28] py-0.5 pl-3">
                      {r.files.length > 0 ? (
                        r.files.map((f) => (
                          <div key={f.path} className="flex items-baseline gap-2.5 py-[3px] font-mono text-[11px] text-tx2">
                            <span className="min-w-0 truncate">{f.path}</span>
                            {f.add !== undefined && (
                              <span className="ml-auto flex flex-none gap-1.5">
                                <i className="not-italic text-olive">+{f.add}</i>
                                {f.del !== undefined && f.del > 0 && <i className="not-italic text-salmon">-{f.del}</i>}
                              </span>
                            )}
                          </div>
                        ))
                      ) : (
                        <div className="py-[3px] text-[11px] text-[#6b6046]">尚无变更</div>
                      )}
                      {r.files.length > 0 && r.files[0].add === undefined && (
                        <div className="py-[3px] text-[10.5px] text-[#6b6046]">±行数未采集（Runner diffstat 待接入）</div>
                      )}
                    </div>
                    <div className="mt-0.5 mb-1.5 ml-6 flex items-center gap-2 pl-3 font-mono text-[10.5px] text-tx2">
                      <span className="grid size-[13px] flex-none place-items-center text-amber [&_svg]:size-[13px]">
                        <DiffIcon />
                      </span>
                      {r.note}
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {view.gates.length > 0 && (
            <>
              <Label>CHANGESET{view.mergeOrderLabel ? ` · ${view.mergeOrderLabel}` : ""}</Label>
              {view.gates.map((g) => {
                const [label, cls] = g.merged ? ["已合并", "text-olive"] : PR_STATE[g.state];
                return (
                  <StaticRow
                    key={g.repo}
                    icon={<PrIcon />}
                    name={g.prUrl ? g.pr : g.repo}
                    nameClass="font-mono"
                    end={
                      <>
                        {g.mergeAllowed && !g.merged && (
                          <i className="text-[11px] not-italic text-olive">✓ 可合并</i>
                        )}
                        <i className={`text-[11.5px] not-italic ${cls}`}>{label}</i>
                      </>
                    }
                  />
                );
              })}
            </>
          )}

          <Label>环境</Label>
          {view.stagingNote && (
            <StaticRow icon={<EnvIcon />} name="预发环境" end={<i className="text-[11px] not-italic text-tx2">{view.stagingNote}</i>} />
          )}
          {view.snapshotLabel && (
            <StaticRow
              icon={<DiffIcon />}
              name="基线快照"
              end={<i className="font-mono text-[11px] not-italic text-tx2">{view.snapshotLabel}</i>}
            />
          )}
          {view.matrixRoom && (
            <StaticRow
              icon={<EnvIcon />}
              name="Matrix 房间"
              end={<i className="font-mono text-[11px] not-italic text-tx2">{view.matrixRoom}</i>}
            />
          )}
          {view.costLabel && (
            <StaticRow icon={<DiffIcon />} name="成本" end={<i className="text-[11px] not-italic text-tx2">{view.costLabel}</i>} />
          )}
          {view.traceId && (
            <StaticRow
              icon={<DiffIcon />}
              name="TRACE"
              end={<i className="font-mono text-[11px] not-italic text-tx2">{view.traceId}</i>}
            />
          )}
          {!view.stagingNote && !view.snapshotLabel && !view.matrixRoom && !view.costLabel && !view.traceId && (
            <div className="px-2.5 py-[3px] text-[11px] text-[#6b6046]">环境信息未采集</div>
          )}

          {view.envProcesses.length > 0 && (
            <>
              <Label>后台进程</Label>
              {view.envProcesses.map((p) => (
                <StaticRow key={p} icon={<TermIcon />} name={p} nameClass="font-mono text-[11px] text-tx2" />
              ))}
            </>
          )}

          <Label>事件时间线</Label>
          <EventTimeline state={events} demo={demo} onFilter={onEventsFilter} onLoadMore={onEventsMore} />
        </div>
      )}
    </aside>
  );
}
