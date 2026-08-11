import { useState } from "react";
import { gates, repoDiffs } from "../data/mock";
import type { GateState } from "../types";
import { ChevronIcon, DiffIcon, EnvIcon, PlusIcon, PrIcon, RepoIcon, TermIcon } from "./icons";

/** 悬浮环境窗（Codex 式）：关联仓库 diff → ChangeSet → 环境 → 后台进程。 */

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

export function EnvPanel({ onToast }: { onToast: (text: string) => void }) {
  const [minimized, setMinimized] = useState(false);
  const [openRepos, setOpenRepos] = useState<Set<string>>(() => new Set([repoDiffs[0].id]));

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
          {repoDiffs.map((r) => {
            const open = openRepos.has(r.id);
            return (
              <div key={r.id}>
                <button className={`${rowBase} hover:bg-amber/5`} onClick={() => toggleRepo(r.id)}>
                  <span className="grid size-4 flex-none place-items-center text-amber [&_svg]:size-4">
                    <RepoIcon />
                  </span>
                  <span className="min-w-0 truncate font-mono">{r.id}</span>
                  <span className="ml-auto flex flex-none items-center gap-[7px] text-[12px]">
                    {r.add + r.del > 0 ? (
                      <>
                        <i className="font-mono text-[11.5px] not-italic text-olive">+{r.add}</i>
                        <i className="font-mono text-[11.5px] not-italic text-salmon">-{r.del}</i>
                      </>
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
                            <span className="ml-auto flex flex-none gap-1.5">
                              <i className="not-italic text-olive">+{f.add}</i>
                              {f.del > 0 && <i className="not-italic text-salmon">-{f.del}</i>}
                            </span>
                          </div>
                        ))
                      ) : (
                        <div className="py-[3px] text-[11px] text-[#6b6046]">尚无变更</div>
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

          <Label>CHANGESET · core → dash / apps → docs</Label>
          {gates.map((g) => {
            const [label, cls] = PR_STATE[g.state];
            return (
              <StaticRow
                key={g.repo}
                icon={<PrIcon />}
                name={g.pr.split(" ")[0]}
                nameClass="font-mono"
                end={<i className={`text-[11.5px] not-italic ${cls}`}>{label}</i>}
              />
            );
          })}

          <Label>环境</Label>
          <StaticRow icon={<EnvIcon />} name="预发环境" end={<i className="text-[11px] not-italic text-tx2">未部署 · 等 core 合并</i>} />
          <StaticRow icon={<DiffIcon />} name="基线快照" end={<i className="font-mono text-[11px] not-italic text-tx2">snap-dlv0042-01</i>} />
          <StaticRow icon={<EnvIcon />} name="Matrix 房间" end={<i className="font-mono text-[11px] not-italic text-tx2">#dlv-0042</i>} />
          <StaticRow icon={<DiffIcon />} name="成本" end={<i className="text-[11px] not-italic text-tx2">1.24M tok · ¥8.42 · 2h09m</i>} />

          <Label>后台进程</Label>
          <StaticRow icon={<TermIcon />} name="repomesh-runner · poll /runner-tasks/next" nameClass="font-mono text-[11px] text-tx2" />
          <StaticRow icon={<TermIcon />} name="otlp-exporter → localhost:3001/v1/traces" nameClass="font-mono text-[11px] text-tx2" />
        </div>
      )}
    </aside>
  );
}
