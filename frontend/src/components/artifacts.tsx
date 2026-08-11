import type { DeliveryView } from "../types";
import { Dag } from "./Dag";

/** 房间流内嵌的结构化 artifact 卡：范围冻结 / DAG / 失败+修复循环+治理拦截 / 审批。 */

const artBase = "mt-2.5 ml-9 max-w-[720px] rounded-hard border p-4";

export function ScopeArtifact({ view }: { view: DeliveryView }) {
  return (
    <div className={`${artBase} border-line bg-panel`}>
      <div className="mb-2.5 border-b border-line pb-2">
        <span className="eyebrow">仓库范围 · 已由你确认冻结</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {view.repos.map((r) => (
          <span key={r.id} className="rounded-hard border border-line px-2 py-px font-mono text-[12px] text-tx">
            {r.id}
          </span>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {view.repos
          .filter((r) => r.evidence !== null)
          .map((r) => (
            <span key={r.id} className="rounded-hard bg-panel-2 px-2 py-0.5 font-mono text-[11px] text-tx2">
              {r.evidence}
            </span>
          ))}
      </div>
      <div className="mt-2.5 border-t border-dashed border-line pt-2 font-mono text-[10.5px] tracking-[0.06em] text-tx2">
        证据：依赖扫描 · 由 Repository Intelligence 保留，范围最终由人确认
      </div>
    </div>
  );
}

export function DagArtifact({ view }: { view: DeliveryView }) {
  return (
    <div className={`${artBase} border-line bg-panel`}>
      <div className="mb-2.5 border-b border-line pb-2">
        <span className="eyebrow">
          跨仓任务 DAG · REV {view.planRev}
          {view.mergeOrderLabel ? ` · 合并顺序 ${view.mergeOrderLabel}` : ""}
        </span>
      </div>
      <div className="grid-amber p-1.5">
        <Dag skin="room" tasks={view.tasks} lanes={view.lanes} />
      </div>
    </div>
  );
}

export function FailArtifact({ view }: { view: DeliveryView }) {
  const task = view.tasks.find((t) => t.status === "repairing" || t.status === "failed" || t.status === "blocked");
  if (!task) return null;
  const denyEvent = view.events.find((e) => e.kind === "deny");
  return (
    <div className={`${artBase} border-[#7a4530] bg-[#251710]`}>
      <div className="mb-2.5 border-b border-[#7a4530] pb-2">
        <span className="eyebrow text-salmon">独立验证 · {task.repo} 门禁受阻</span>
      </div>
      <p className="text-[13px] text-[#d8b7a4]">
        {task.detail ?? `${task.title} 验证未通过。`}
        {task.escalated ? " 已按恢复计划升级人工接管。" : ""}
      </p>
      {task.repair.length > 0 && (
        <div className="mt-2.5 border-l-2 border-salmon bg-[#2b1c12] px-3 py-2 text-[12px] text-tx">
          {task.repair.map((r) => (
            <div key={r.at}>
              <span className="mr-2 font-mono text-tx2">{r.at}</span>
              {r.what}
            </div>
          ))}
        </div>
      )}
      {denyEvent && (
        <div className="mt-2.5 rounded-hard border border-dashed border-[#8a6a35] bg-[#26200f] px-3 py-2 text-[12px] text-[#c9a96a]">
          治理拦截：{denyEvent.text.replace(/^治理：/, "")}
        </div>
      )}
    </div>
  );
}

export function ApproveArtifact({ view, onApprove }: { view: DeliveryView; onApprove: () => void }) {
  const approve = view.decisions.find((d) => d.kind === "approve");
  if (!approve) return null;
  const gate = view.gates.find((g) => g.state === "open");
  const checkNote = gate ? gate.checks.map((c) => `${c.name} ${c.note}`).join(" · ") : "";
  return (
    <div className={`${artBase} border-[#5c6b35] bg-[#1c2010]`}>
      <div className="mb-2.5 border-b border-[#5c6b35] pb-2">
        <span className="eyebrow text-olive">发布门禁 · 等待人工审批</span>
      </div>
      <p className="text-[13px] text-[#c6cfa4]">
        <b className="text-[#d9e6a8]">{approve.title}</b> — {approve.body}
        {checkNote && <span className="mt-1 block font-mono text-[11px] text-[#a8b784]">{checkNote}</span>}
      </p>
      <button
        className="mt-3 rounded-hard bg-olive px-4 py-2 text-[13px] font-extrabold tracking-[0.04em] text-[#161a08] hover:bg-[#a7b76a]"
        onClick={onApprove}
      >
        批准合并（打开授权单）
      </button>
    </div>
  );
}
