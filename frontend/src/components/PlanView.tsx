import { contract, delivery, rollback, tasks } from "../data/mock";
import type { TaskStatus } from "../types";
import { Dag } from "./Dag";

/** DAG · 计划视图 —— 阿波罗飞行计划风格的奶油纸面文档。 */

const STATUS_WORD: Record<TaskStatus, string> = {
  succeeded: "COMPLETE",
  running: "IN PROGRESS",
  repairing: "REPAIR 2/3",
  pending: "HOLD",
  failed: "FAILED",
};

const STATUS_COLOR: Record<TaskStatus, string> = {
  succeeded: "text-[#5b6d2c]",
  running: "text-[#3d617a]",
  repairing: "text-[#a3702e]",
  pending: "text-[#8a7f63]",
  failed: "text-paper-neg",
};

function SectionBar({ num, title, note, salmon }: { num: string; title: string; note?: string; salmon?: boolean }) {
  return (
    <div className="mb-2.5 flex items-center gap-2.5">
      <span
        className={`px-2.5 py-0.5 font-mono text-[16px] font-extrabold text-cream ${salmon ? "bg-paper-neg" : "bg-[#17130a]"}`}
      >
        {num}
      </span>
      <h3 className="text-[13px] font-extrabold tracking-[0.22em]">{title}</h3>
      {note && <span className="ml-auto font-mono text-[10.5px] tracking-[0.08em] text-paper-dim">{note}</span>}
    </div>
  );
}

function Item({
  n,
  neg,
  children,
}: {
  n: string;
  neg?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline gap-3 border-b border-[#17130a]/16 py-1.5 text-[13px]">
      <span
        className={`flex-none px-[7px] py-px font-mono text-[10.5px] font-bold text-cream ${neg ? "bg-paper-neg" : "bg-[#17130a]"}`}
      >
        {n}
      </span>
      {children}
    </div>
  );
}

export function PlanView() {
  return (
    <div className="flex-1 overflow-y-auto px-[26px] pt-[22px] pb-[30px]">
      <div className="mx-auto max-w-[840px] rounded-hard bg-cream px-[30px] pt-[26px] pb-[34px] text-paper-ink shadow-[0_16px_44px_rgba(0,0,0,0.5)]">
        <div className="flex items-start gap-3.5 border-b-[3px] border-[#17130a] pb-3">
          <span className="bg-[#17130a] px-2.5 py-1 font-mono text-[14px] font-extrabold tracking-[0.06em] text-cream">
            {delivery.id}
          </span>
          <div>
            <h2 className="text-[17px] font-extrabold tracking-[0.02em]">交付计划 · {delivery.title}</h2>
            <span className="mt-0.5 block font-mono text-[10.5px] tracking-[0.12em] text-paper-dim">
              REV 2 · CONTRACT V3 FROZEN · {delivery.createdAt.slice(0, 10)}
            </span>
          </div>
        </div>

        <section className="mt-6">
          <SectionBar num="1.0" title="目标与验收" />
          <p className="max-w-[640px] pt-1 pb-2 text-[13.5px]">{contract.goal}</p>
          {contract.acceptance.map((a, i) => (
            <Item key={a} n={`1.${i + 1}`}>
              <span>{a}</span>
            </Item>
          ))}
          <Item n="非目标" neg>
            <span className="text-paper-dim">{contract.nonGoals.join("；")}</span>
          </Item>
        </section>

        <section className="mt-6">
          <SectionBar num="2.0" title="变更范围" />
          <div className="mb-2 flex flex-wrap gap-1.5">
            {contract.scope.repositories.map((r) => (
              <span key={r} className="bg-[#17130a] px-2.5 py-[3px] font-mono text-[11px] tracking-[0.04em] text-cream">
                {r}
              </span>
            ))}
          </div>
          <Item n="2.1">
            <span className="font-mono text-[11.5px]">允许 {contract.scope.allowedPaths.join("  ")}</span>
          </Item>
          <Item n="2.2" neg>
            <span className="font-mono text-[11.5px] text-paper-neg">
              禁止 {contract.scope.forbiddenPaths.join("  ")}
            </span>
          </Item>
        </section>

        <section className="mt-6">
          <SectionBar num="3.0" title="任务 DAG" note="MERGE ORDER: core → dashboard / apps → docs" />
          <div className="grid-paper border border-[#17130a]/25 p-2">
            <Dag skin="paper" />
          </div>
        </section>

        <section className="mt-6">
          <SectionBar num="4.0" title="执行计划" />
          {tasks.map((t, i) => (
            <Item key={t.id} n={`4.${i + 1}`}>
              <span className="flex-none font-mono font-extrabold">{t.id}</span>
              <span className="w-[138px] flex-none font-mono text-[11px] text-paper-dim">{t.repo}</span>
              <span className="min-w-0">{t.title}</span>
              <span className="ml-auto flex-none font-mono text-[11px] text-paper-dim">{t.agent}</span>
              <span className={`flex-none font-mono text-[10.5px] font-extrabold tracking-[0.08em] ${STATUS_COLOR[t.status]}`}>
                {STATUS_WORD[t.status]}
              </span>
            </Item>
          ))}
        </section>

        <section className="mt-6">
          <SectionBar num="5.0" title="回滚预案" salmon />
          {rollback.map((r, i) => (
            <Item key={r} n={`5.${i + 1}`} neg>
              <span>{r}</span>
            </Item>
          ))}
        </section>
      </div>
    </div>
  );
}
