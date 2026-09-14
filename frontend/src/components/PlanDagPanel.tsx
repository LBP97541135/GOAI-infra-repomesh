import { useEffect, useRef, useState } from "react";
import type { PlanGraphEdgeView, RepositoryPlanView, TaskDisplayStatus } from "../api/contract";
import type { DagExecutionView } from "../types";
import { unverifiedMarkerLabel } from "../display";
import { UnverifiedMarker } from "./AgentVerificationBlock";

/** 图形化 DAG 面板（批次 C-2）。数据源是既有端点
 *  `GET /issues/{id}/repositories/{repo}/plan`（契约 v0.2 §5.4/§5.5），与 RoomView
 *  文本版计划纸面同源——本面是它的图形化呈现。
 *
 *  2026-09-08 视觉定稿（与用户逐项确认）：
 *   - **紧凑单行胶囊节点**（高 26px：状态色点 + 名称）；状态全名/任务数/未验证数
 *     全部进 hover，节点上只留最小留痕（未解析、锚点 ◆）；
 *   - **等比缩放适配**：整图按容器宽度缩放（只缩不放），典型 3~5 批次一屏放下，
 *     超大图才出滚动；
 *   - **hover 高亮上下游**：悬停节点时它的依赖边与下游边加亮加粗、直连邻居保持，
 *     无关节点与边淡出——多仓依赖关系一眼可读；
 *   - **配色走主题令牌**：白卡 + 发丝线 + 微阴影（浅色即白浮卡观感；深色自动是
 *     深棕面板），「进行中」在浅色下用信息蓝（见 index.css 的
 *     `--color-status-running`，深色维持琥珀不变）。
 *
 *  页脚方法论自述（粒度/边来源/锚点/未验证长文/着色来源/投影边界）按用户裁决退役；
 *  连线契约加注（粗线 + hover 接口与约定）保留。
 *
 *  红线：不派生任何状态——`display_status` 是读模型 §5.1 算好的，本面只把字面值
 *  上色并原样印进 hover；配色全部取 `index.css` 既有令牌，不新增颜色语义。 */

/* ── 泳道几何（单位 px，SVG 与 HTML 覆盖层共用同一套坐标） ───────────────── */
const NODE_W = 150;
const NODE_H = 26;
const COL_GAP = 46; // 列间距要装得下箭头，太窄会让边看起来贴在节点上
const ROW_GAP = 12;
const PAD = 12;
const HEAD_H = 16; // 批次标题行

const nodeX = (col: number) => PAD + col * (NODE_W + COL_GAP);
const nodeY = (row: number) => PAD + HEAD_H + row * (NODE_H + ROW_GAP);

/** 节点的稳定标识＝`name + batch_index`。**不能用 `repository_id`**：契约 §5.4
 *  勘正后它可为 null（catalog 无此名 / issue 域外重名歧义），多个未解析节点会
 *  塌到同一个 key 上。hover 邻接也按它寻址。 */
const nodeKey = (node: { name: string; batch_index: number }) => `${node.batch_index}:${node.name}`;

/** 边来源的中文措辞（迁移 4）。三者是**不同性质的事实**，不合并：
 *  `scan` 是从代码里扫出来的依赖，`llm` 是集成时模型判定的，`tm` 是人工批次
 *  顺序反推的。一条边可信到什么程度，取决于它是哪一种。 */
const EDGE_SOURCE_LABEL: Record<string, string> = {
  scan: "扫描（代码依赖）",
  llm: "集成模型判定",
  tm: "人工批次顺序派生",
};

interface Placed {
  node: RepositoryPlanView["dag"]["nodes"][number];
  col: number;
  row: number;
}

/* ── 执行态皮肤（C-4）─────────────────────────────────────────────────────── */

/** **展示皮肤，不是状态映射。** 状态映射唯一实现在读模型（契约 v0.1 §5.1，后端
 *  7 态 → 展示 6 态）；本表只把已经给出的 6 个字面值分到皮肤上，不参与任何判定。
 *  Record 收窄到契约枚举：读模型将来多出第 7 个展示态时，这里缺项即编译错误。
 *
 *  分桶（设计定稿 ③）：橄榄 = 已交付 / 进行中 = `--color-status-running`
 *  （深琥珀 · 浅信息蓝，见 index.css）/ 弱灰 = 等待 / 赭红 = 失败。胶囊只上一圈
 *  细描边加一层极浅底色，重心在色点上——满色块在白底上糊成一团。 */
const EXEC_SKIN: Record<TaskDisplayStatus, string> = {
  succeeded: "border-olive/50 bg-[color-mix(in_oklab,var(--color-olive)_8%,var(--color-panel))]",
  running:
    "border-[var(--color-status-running)]/50 bg-[color-mix(in_oklab,var(--color-status-running)_8%,var(--color-panel))]",
  repairing:
    "border-[var(--color-status-running)]/50 bg-[color-mix(in_oklab,var(--color-status-running)_8%,var(--color-panel))]",
  pending: "border-line bg-panel",
  blocked: "border-line bg-panel",
  failed: "border-salmon/50 bg-[color-mix(in_oklab,var(--color-salmon)_8%,var(--color-panel))]",
};

const STATUS_DOT: Record<TaskDisplayStatus, string> = {
  succeeded: "bg-olive",
  running: "bg-[var(--color-status-running)]",
  repairing: "bg-[var(--color-status-running)]",
  pending: "bg-paper-dim",
  blocked: "bg-paper-dim",
  failed: "bg-salmon",
};

/** 按 `batch_index` 分列。列取自节点自身而非 `execution_batches`——两者是同一份
 *  投影（服务端遍历 execution_batches 生成节点，batch_index 就是那个下标）。
 *  列号用**批次值排序后的名次**，即便某个 batch_index 空缺也不会留出空列。 */
function layout(nodes: RepositoryPlanView["dag"]["nodes"]): { placed: Placed[]; batches: number[]; rows: number } {
  const batches = [...new Set(nodes.map((n) => n.batch_index))].sort((a, b) => a - b);
  const filled = new Map<number, number>();
  const placed = nodes.map((node) => {
    const col = batches.indexOf(node.batch_index);
    const row = filled.get(col) ?? 0;
    filled.set(col, row + 1);
    return { node, col, row };
  });
  return { placed, batches, rows: Math.max(1, ...filled.values()) };
}

function NodeBox({
  placed,
  execution,
  dimmed,
  onHover,
}: {
  placed: Placed;
  execution: DagExecutionView | null;
  dimmed: boolean;
  onHover: (key: string | null) => void;
}) {
  const { node } = placed;
  const unresolved = node.repository_id === null;

  /** 本仓在本轮的执行态。三种「没有」互不相同，压成一个会撒谎：
   *   - `execution === null`：未物化 / 本轮聚合没取到——**无事实可着色**；
   *   - 本轮没有这个仓的任务（计数 0）：计划里有它，执行面还没有它；
   *   - 有多条任务且态不一致（值为 null）：读模型没有给出仓级结论，不挑一条充数。 */
  const taskCount = execution && node.repository_id ? (execution.taskCountByRepository[node.repository_id] ?? 0) : 0;
  const status = execution && node.repository_id ? (execution.byRepository[node.repository_id] ?? null) : null;
  const colored = !unresolved && status !== null;

  /** A-18：本仓有几条任务是 agent 自述「未验证」的——不换状态色，另加琥珀标记。 */
  const unverified =
    execution && node.repository_id
      ? (execution.unverifiedCountByRepository[node.repository_id] ?? 0)
      : 0;
  const blockerCount =
    execution && node.repository_id
      ? (execution.blockerCountByRepository[node.repository_id] ?? 0)
      : 0;
  /** A-18 第四面：失败理由（Runner 原文）。 */
  const failureReasons =
    execution && node.repository_id
      ? (execution.failureReasonsByRepository[node.repository_id] ?? [])
      : [];

  const skin = unresolved
    ? "border-dashed border-salmon/70 bg-panel"
    : colored
      ? EXEC_SKIN[status]
      : node.is_focus
        ? "border-amber/60 bg-[color-mix(in_oklab,var(--color-amber)_10%,var(--color-panel))]"
        : "border-line bg-panel";

  const baseTitle = unresolved
    ? `${node.name}：catalog 中查无此仓库——名字未注册，或在本 issue 域外重名歧义（域内优先后仍无唯一解），服务端不猜。`
    : colored
      ? `${node.name} · 本轮任务展示态 ${status}（读模型 §5.1 算出的 display_status，界面只上色）`
      : node.name;

  const title = [
    baseTitle,
    !unresolved && execution && taskCount === 0 ? "本轮还没有这个仓的任务（计划内有它，执行面还没有它）。" : null,
    !unresolved && execution && taskCount > 1 && status === null ? `${taskCount} 条任务态不一致，读模型未给出仓级结论。` : null,
    unverified > 0
      ? `${unverifiedMarkerLabel(blockerCount)}：本仓 ${unverified} 条任务没有可核验的执行记录（agent 自述，契约 §5.4）。原话在「查看证据」里。`
      : null,
    ...failureReasons.map((reason) => `失败理由（Runner 原文）：${reason}`),
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <div
      className={`absolute flex items-center gap-1.5 overflow-hidden rounded-full border px-2 transition-opacity ${skin} ${
        dimmed ? "opacity-25" : ""
      }`}
      style={{ left: nodeX(placed.col), top: nodeY(placed.row), width: NODE_W, height: NODE_H }}
      title={title}
      onMouseEnter={() => onHover(nodeKey(node))}
      onMouseLeave={() => onHover(null)}
    >
      {/* 状态色点：颜色即皮肤，字面值印在后面——读者不必反查配色表 */}
      {unresolved ? (
        <span className="size-2 flex-none rounded-full border border-salmon" />
      ) : colored ? (
        <span className={`size-2 flex-none rounded-full ${STATUS_DOT[status]}`} />
      ) : (
        <span className="size-2 flex-none rounded-full border border-paper-dim/60" />
      )}
      <span className={`min-w-0 flex-1 truncate font-mono text-[11px] leading-none font-semibold ${unresolved ? "text-salmon" : "text-tx"}`}>
        {node.name}
      </span>
      {/* A-18：与状态并排、不覆盖它——「跑成了」和「没验证」都是真的 */}
      {unverified > 0 && (
        <UnverifiedMarker compact blockerCount={blockerCount} title={`${unverified} 条任务未验证（agent 自述）`} />
      )}
      {/* is_focus 只在 id 非 null 时可能为 true，故与「未解析」互斥 */}
      {node.is_focus && <span className="flex-none font-mono text-[9px] leading-none text-amber">◆</span>}
      {unresolved && <span className="flex-none font-mono text-[9px] leading-none font-bold text-salmon">未解析</span>}
    </div>
  );
}

function DagCanvas({
  dag,
  graphEdges,
  execution,
}: {
  dag: RepositoryPlanView["dag"];
  graphEdges: PlanGraphEdgeView[] | null;
  execution: DagExecutionView | null;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  /** 容器实测宽度（等比缩放的基准）；null = 尚未量到，先按原尺寸画。 */
  const [availW, setAvailW] = useState<number | null>(null);
  /** hover 高亮的节点（nodeKey）。非 null 时：它的直连边加亮，无关节点/边淡出。 */
  const [hovered, setHovered] = useState<string | null>(null);

  // 容器宽度用 ResizeObserver 跟：侧栏开合、窗口缩放都会改它
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const update = () => setAvailW(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const { placed, batches, rows } = layout(dag.nodes);
  const width = PAD * 2 + batches.length * NODE_W + Math.max(0, batches.length - 1) * COL_GAP;
  const gridBottom = PAD + HEAD_H + rows * NODE_H + Math.max(0, rows - 1) * ROW_GAP;

  /** 边按 `repository_id` 寻址。同一 id 理论上只出现在一个批次里；真出现重复时
   *  取先出现的那个位置——画一条到「其中一个」的线，好过整条边消失。 */
  const byId = new Map<string, Placed>();
  for (const p of placed) if (p.node.repository_id !== null && !byId.has(p.node.repository_id)) byId.set(p.node.repository_id, p);

  /** 名字 → 边语义。**只索引 confirmed 边**：candidate 是待确认的扫描边，
   *  没有进拓扑投影，拿它给一条已投影的连线加注就是把待定说成已定。 */
  const pairKey = (fromName: string, toName: string) => `${fromName}\n${toName}`;
  const semanticByPair = new Map<string, PlanGraphEdgeView>();
  for (const edge of graphEdges ?? []) {
    if (edge.status === "confirmed") semanticByPair.set(pairKey(edge.from, edge.to), edge);
  }
  const semanticsOf = (fromName: string, toName: string) =>
    semanticByPair.get(pairKey(fromName, toName)) ?? null;

  const resolved = dag.edges
    .map((edge) => ({ from: byId.get(edge.from_repository_id), to: byId.get(edge.to_repository_id) }))
    // 服务端保证两端已解析且都落在 nodes 内（§5.4），这里的判空只是不让任何
    // 意外形状把整面炸掉。
    .filter((e): e is { from: Placed; to: Placed } => Boolean(e.from && e.to));

  // hover 邻接：点亮 hover 节点的直连边与两端节点
  const litEdgeKeys = new Set<string>();
  const neighborKeys = new Set<string>();
  if (hovered !== null) {
    for (const e of resolved) {
      const fk = nodeKey(e.from.node);
      const tk = nodeKey(e.to.node);
      if (fk === hovered || tk === hovered) {
        litEdgeKeys.add(`${fk}->${tk}`);
        neighborKeys.add(fk);
        neighborKeys.add(tk);
      }
    }
  }

  /** **跨批次边要绕行**：跨越 ≥2 列的边如果直着画，会从中间那一列的节点身上穿过去，
   *  读起来就成了「A→中间仓→C」。这类边改走图底部的绕行道，多条时按槽位错开。 */
  const skipping = resolved.filter((e) => e.to.col - e.from.col > 1);
  const laneSlots = Math.min(skipping.length, 4);
  const laneY = (i: number) => gridBottom + 10 + (i % Math.max(1, laneSlots)) * 9;
  const height = (laneSlots > 0 ? laneY(laneSlots - 1) + 6 : gridBottom) + PAD;

  // 等比缩放适配：只缩不放（小图放大只会糊），超大图保留横向滚动
  const scale = availW === null || availW >= width ? 1 : availW / width;

  return (
    <div ref={wrapRef} className="w-full" style={{ height: Math.round(height * scale) }}>
      <div className="relative origin-top-left" style={{ width, height, transform: `scale(${scale})` }}>
        <svg className="absolute inset-0" width={width} height={height} aria-hidden>
          <defs>
            <marker
              id="plan-dag-arrow"
              markerWidth="7"
              markerHeight="7"
              refX="6"
              refY="3"
              orient="auto"
              markerUnits="userSpaceOnUse"
            >
              <path d="M0,0 L6,3 L0,6 Z" className="fill-tx2" />
            </marker>
          </defs>

          {resolved.map(({ from, to }) => {
            // 边语义按**仓库名**匹配：graph_edges 两端存的是名字（与
            // execution_batches 同口径），而这里的连线按 id 寻址。名字是两者
            // 唯一的公共键。
            const semantic = semanticsOf(from.node.name, to.node.name);
            const x1 = nodeX(from.col) + NODE_W;
            const y1 = nodeY(from.row) + NODE_H / 2;
            const x2 = nodeX(to.col);
            const y2 = nodeY(to.row) + NODE_H / 2;
            const ek = `${nodeKey(from.node)}->${nodeKey(to.node)}`;
            const lit = hovered !== null && litEdgeKeys.has(ek);
            const dim = hovered !== null && !lit;
            const span = to.col - from.col;
            const skipIndex = skipping.findIndex((e) => e.from === from && e.to === to);
            let d: string;
            if (span > 1) {
              // 绕行道：右出 → 下沉到底部车道 → 横穿 → 抬回目标左侧
              const lane = laneY(skipIndex < 0 ? 0 : skipIndex);
              d = `M ${x1} ${y1} C ${x1 + 20} ${y1}, ${x1 + 20} ${lane}, ${x1 + 40} ${lane} L ${x2 - 40} ${lane} C ${x2 - 20} ${lane}, ${x2 - 20} ${y2}, ${x2} ${y2}`;
            } else if (span > 0) {
              // 相邻批次：右出左入的横向贝塞尔
              d = `M ${x1} ${y1} C ${x1 + COL_GAP / 2} ${y1}, ${x2 - COL_GAP / 2} ${y2}, ${x2} ${y2}`;
            } else {
              // 同列或回指（execution_batches 的语义下不该出现）退化成竖直连线，
              // 不假装它是一条正常的层间边。
              d = `M ${nodeX(from.col) + NODE_W / 2} ${nodeY(from.row) + NODE_H} L ${nodeX(to.col) + NODE_W / 2} ${nodeY(to.row)}`;
            }
            return (
              <path
                key={ek}
                d={d}
                fill="none"
                // 带契约的边画实一点：它比一条纯执行顺序依赖多一份约定。
                // 只用粗细区分，不新增颜色语义。
                className={lit ? "stroke-tx" : "stroke-tx2/70"}
                strokeWidth={semantic?.interface ? (lit ? 2.2 : 1.9) : lit ? 1.7 : 1.2}
                opacity={dim ? 0.18 : 1}
                markerEnd="url(#plan-dag-arrow)"
              >
                {semantic && (
                  // 原生 <title>：hover 出提示，且进可访问性树。
                  // 没有 interface 的边只报来源，不编一个契约名出来。
                  <title>
                    {[
                      `${from.node.name} → ${to.node.name}`,
                      semantic.interface ? `接口：${semantic.interface}` : null,
                      semantic.agreement ? `约定：${semantic.agreement}` : null,
                      `来源：${EDGE_SOURCE_LABEL[semantic.source] ?? semantic.source}`,
                    ]
                      .filter(Boolean)
                      .join("\n")}
                  </title>
                )}
              </path>
            );
          })}
        </svg>

        {batches.map((batch, col) => (
          <div
            key={batch}
            className="absolute font-mono text-[9px] font-bold tracking-[0.16em] text-tx3 uppercase"
            style={{ left: nodeX(col), top: PAD - 2, width: NODE_W }}
          >
            批次 {batch + 1}
          </div>
        ))}

        {placed.map((p) => (
          <NodeBox
            key={nodeKey(p.node)}
            placed={p}
            execution={execution}
            dimmed={hovered !== null && !neighborKeys.has(nodeKey(p.node))}
            onHover={setHovered}
          />
        ))}
      </div>
    </div>
  );
}

/** 图例行。**两套图例按有没有执行态事实切换**——未物化时摆一排执行态色点，等于
 *  给一张没有执行事实的图配一本用不上的色谱，读者会以为自己在看运行状态。 */
function Legend({ execution }: { execution: DagExecutionView | null }) {
  const dot = (cls: string, label: string) => (
    <span key={label} className="flex items-center gap-1">
      <i className={`inline-block size-2 rounded-full ${cls}`} />
      {label}
    </span>
  );

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] text-tx2">
      {execution ? (
        <>
          <span className="font-bold tracking-[0.1em] uppercase">执行态 · {execution.roundLabel}</span>
          {dot("bg-olive", "已交付 succeeded")}
          {dot("bg-[var(--color-status-running)]", "进行中 running / repairing")}
          {dot("bg-paper-dim", "等待 pending / blocked")}
          {dot("bg-salmon", "失败 failed")}
          {dot("border border-dashed border-salmon bg-transparent", "未解析（catalog 无此仓）")}
          {/* A-18：不是第五个执行态，是贴在任何一个态上的标记，所以图例里也另起一说 */}
          {dot("border border-amber bg-transparent", "未验证（标记，非状态）")}
        </>
      ) : (
        <>
          <span className="font-bold tracking-[0.1em] uppercase">结构</span>
          {dot("border border-amber/60 bg-[color-mix(in_oklab,var(--color-amber)_10%,var(--color-panel))]", "锚点仓")}
          {dot("border border-line bg-panel", "计划内仓库")}
          {dot("border border-dashed border-salmon bg-transparent", "未解析（catalog 无此仓）")}
        </>
      )}
    </div>
  );
}

/** 面板取数三态 + 「无计划快照」这一态。404 **不是错误**：issue 尚未生成计划
 *  就是这个形态，必须说出来而不是把区块藏掉或摆一张空图假装有计划。 */
export type PlanDagState =
  | { status: "loading" }
  | { status: "absent"; reason: string }
  | { status: "error"; message: string }
  | {
      status: "ready";
      plan: RepositoryPlanView;
      /** 迁移 4：该版快照的计划层边（含 interface/agreement）。**null = 没取到**
       *  （老快照 graph_edges 为空 / 端点 404 / 回放模式），此时连线照画、只是
       *  没有语义可标——这一层是给既有连线加注的，不是画图的前提。 */
      graphEdges: PlanGraphEdgeView[] | null;
    };

export function PlanDagPanel({
  state,
  execution,
  onRetry,
}: {
  state: PlanDagState;
  /** C-4 执行态着色的输入。`null` = 尚未物化（无轮次）或本轮聚合没取到，
   *  此时节点维持结构三视觉——没有事实就不上色。 */
  execution: DagExecutionView | null;
  onRetry: () => void;
}) {
  return (
    <>
      {/* 面板标题由承载方（PlanDagCapsule 胶囊）提供，这里只渲染状态与图本身 */}

      {state.status === "loading" && <p className="py-4 text-[12px] text-tx2">计划纸面加载中…</p>}

      {state.status === "absent" && <p className="text-[12px] text-tx3">{state.reason}</p>}

      {state.status === "error" && (
        <p className="text-[12px] text-salmon">
          计划纸面取用失败：{state.message}
          <button className="pl-2 text-tx2 underline hover:text-amber-hi" onClick={onRetry}>
            重试
          </button>
        </p>
      )}

      {state.status === "ready" && (
        <PlanDagSheet plan={state.plan} graphEdges={state.graphEdges} execution={execution} />
      )}
    </>
  );
}

function PlanDagSheet({
  plan,
  graphEdges,
  execution,
}: {
  plan: RepositoryPlanView;
  graphEdges: PlanGraphEdgeView[] | null;
  execution: DagExecutionView | null;
}) {
  // 头部统计行（版本/节点/批次）由胶囊承载，面板只画图——浮窗里不再留空带
  return (
    <div className="rounded-hard border border-line bg-panel px-3 py-2 text-tx shadow-card">
      {plan.dag.nodes.length === 0 ? (
        // 快照在、批次为空：这是真实形态之一，说出来而不是画一张空画布
        <p className="py-2 font-mono text-[11.5px] text-tx3">
          本计划快照（v{plan.plan_version}）没有任何执行批次，无可绘制的节点。
        </p>
      ) : (
        <DagCanvas dag={plan.dag} graphEdges={graphEdges} execution={execution} />
      )}

      {plan.dag.nodes.length > 0 && (
        <div className="mt-1.5">
          <Legend execution={execution} />
        </div>
      )}
    </div>
  );
}
