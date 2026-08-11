import { repos, tasks } from "../data/mock";
import { Dag } from "./Dag";

/** 房间流内嵌的结构化 artifact 卡：范围冻结 / DAG / 失败+修复循环+治理拦截 / 审批。 */

const artBase = "mt-2.5 ml-9 max-w-[720px] rounded-hard border p-4";

export function ScopeArtifact() {
  return (
    <div className={`${artBase} border-line bg-panel`}>
      <div className="mb-2.5 border-b border-line pb-2">
        <span className="eyebrow">仓库范围 · 已由你确认冻结</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {repos.map((r) => (
          <span key={r.id} className="rounded-hard border border-line px-2 py-px font-mono text-[12px] text-tx">
            {r.id}
          </span>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {repos.map((r) => (
          <span key={r.id} className="rounded-hard bg-panel-2 px-2 py-0.5 font-mono text-[11px] text-tx2">
            {r.evidence}
          </span>
        ))}
      </div>
      <div className="mt-2.5 border-t border-dashed border-line pt-2 font-mono text-[10.5px] tracking-[0.06em] text-tx2">
        证据：依赖扫描 17 FILES · 6 SYMBOLS · 2 API CONTRACTS
      </div>
    </div>
  );
}

export function DagArtifact() {
  return (
    <div className={`${artBase} border-line bg-panel`}>
      <div className="mb-2.5 border-b border-line pb-2">
        <span className="eyebrow">跨仓任务 DAG · REV 2 · 合并顺序 core → dashboard / apps → docs</span>
      </div>
      <div className="grid-amber p-1.5">
        <Dag skin="room" />
      </div>
    </div>
  );
}

export function FailArtifact() {
  const t3 = tasks.find((t) => t.id === "T3")!;
  return (
    <div className={`${artBase} border-[#7a4530] bg-[#251710]`}>
      <div className="mb-2.5 border-b border-[#7a4530] pb-2">
        <span className="eyebrow text-salmon">独立验证 · dashboard 门禁受阻</span>
      </div>
      <p className="text-[13px] text-[#d8b7a4]">
        隐藏验收测试 3/9 失败：<b className="text-[#e8a184]">OrderPriceOverrideNote 空值渲染崩溃</b>。修复循环第{" "}
        {t3.attempt}/3 次，范围限定 src/orders/components/**。再失败一次将按契约升级人工接管。
      </p>
      <div className="mt-2.5 border-l-2 border-salmon bg-[#2b1c12] px-3 py-2 text-[12px] text-tx">
        {t3.repair!.map((r) => (
          <div key={r.at}>
            <span className="mr-2 font-mono text-tx2">{r.at}</span>
            {r.what}
          </div>
        ))}
      </div>
      <div className="mt-2.5 rounded-hard border border-dashed border-[#8a6a35] bg-[#26200f] px-3 py-2 text-[12px] text-[#c9a96a]">
        治理拦截：开发 Agent 尝试自行运行验收测试被权限层拒绝——自证不算数，验收由 QA Guardian 受控执行。
      </div>
    </div>
  );
}

export function ApproveArtifact({ onApprove }: { onApprove: () => void }) {
  return (
    <div className={`${artBase} border-[#5c6b35] bg-[#1c2010]`}>
      <div className="mb-2.5 border-b border-[#5c6b35] pb-2">
        <span className="eyebrow text-olive">发布门禁 · 等待人工审批</span>
      </div>
      <p className="text-[13px] text-[#c6cfa4]">
        <b className="text-[#d9e6a8]">saleor-core 已通过全部 5 项门禁</b>
        （单测 412 · 集成 58 · 隐藏 9/9 · 安全 0 高危 · 独立 Review 通过）。合并顺序第 1
        位，批准后按序推进，任何关键仓失败整体不会标记成功。
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
