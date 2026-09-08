import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { DagExecutionView } from "../types";
import { ErrorBoundary } from "./ErrorBoundary";
import { PlanDagPanel, type PlanDagState } from "./PlanDagPanel";

/** 物化后的胶囊进度读数：N/M 仓已交付。数的是**仓**（byRepository 的归拢结论），
 *  不是任务——任务数在面板头部有，胶囊只给一眼可读的进度。态不一致（null）的仓
 *  不计入已交付：读模型没说交付了，界面不替它说。 */
function capsuleProgress(execution: DagExecutionView | null): string | null {
  if (!execution) return null;
  const repos = Object.keys(execution.taskCountByRepository);
  if (repos.length === 0) return null;
  const delivered = repos.filter((id) => execution.byRepository[id] === "succeeded").length;
  return `${delivered}/${repos.length} 仓已交付`;
}

/** 计划 DAG 的「任务清单胶囊」（参照 ZCode 顶部 todo 胶囊，2026-09-08 用户定稿）。
 *
 *  收起 = 一枚椭圆读数胶囊，钉在顶栏右侧（原「N 仓 · N 轮」的位置）：计划版本、
 *  节点/批次规模，物化后追加「N/M 仓已交付」进度；展开 = 从胶囊下方向左浮出的
 *  DAG 面板（等比缩放适配面板宽度）。点胶囊或点面板外任意处收起。
 *
 *  absent / 首次加载中胶囊整个不出现：没有图可看，摆一枚空胶囊就是「看得见的
 *  都属实」的反面。取用失败仍出现（鲑红点 + 面板内重试入口）——失败要能被看见。
 *
 *  渲染失败由区块级 ErrorBoundary 接住：只有这一块塌，对话流照常。 */
export function PlanDagCapsule({
  state,
  execution,
  onRetry,
  resetKey,
}: {
  state: PlanDagState;
  /** C-4 执行态着色与胶囊进度读数的输入；null = 尚未物化。 */
  execution: DagExecutionView | null;
  onRetry: () => void;
  /** 换 issue 即复位（收起 + 错误边界复位），不把上一单的错误挂到这一单头上。 */
  resetKey: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // 换 issue 即收起：A 单展开着切到 B 单，悬着的面板内容已换血，收起最诚实
  useEffect(() => setOpen(false), [resetKey]);

  // 点外部收起（与吸底输入框同一套 mousedown 监听）
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  if (state.status === "absent" || state.status === "loading") return null;

  const progress = capsuleProgress(execution);

  return (
    <div ref={wrapRef} className="relative ml-auto z-20">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-2 rounded-full border bg-panel px-3 py-1 font-mono text-[10.5px] shadow-card transition-colors ${
          open ? "border-amber/60" : "border-line hover:border-amber/60"
        }`}
        title={open ? "收起计划 DAG" : "展开计划 DAG"}
      >
        <span className={`size-1.5 flex-none rounded-full ${state.status === "error" ? "bg-salmon" : "bg-olive"}`} />
        {state.status === "ready" ? (
          <span className="text-tx3">
            v{state.plan.plan_version} · {state.plan.dag.nodes.length} 节点 · {state.plan.execution_batches.length} 批次
            {progress ? ` · ${progress}` : ""}
          </span>
        ) : (
          <span className="text-tx3">取用失败</span>
        )}
        <span className="flex-none text-tx3"><ChevronDown size={12} strokeWidth={1.5} className={open ? "rotate-180" : ""} /></span>
      </button>

      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] w-[min(880px,72vw)] rounded-hard border border-line bg-panel text-tx shadow-float">
          <div className="max-h-[min(68vh,560px)] overflow-y-auto p-2">
            <ErrorBoundary block="计划 DAG" resetKey={resetKey}>
              <PlanDagPanel state={state} execution={execution} onRetry={onRetry} />
            </ErrorBoundary>
          </div>
        </div>
      )}
    </div>
  );
}
