import type { RepositoryPlanView, TaskDisplayStatus } from "../api/contract";
import type { DagExecutionView } from "../types";

/** 图形化 DAG 面板（批次 C-2，设计定稿 `full-loop-gui-design-20260812.md` ③）。
 *
 *  数据源是既有端点 `GET /issues/{id}/repositories/{repo}/plan`（契约 v0.2 §5.4/§5.5），
 *  即 RoomView 里文本版计划纸面（PlanPaper）的同一份投影——本面是它的图形化升级，
 *  落位在 issue 详情页（IA 裁决：不加新的顶层导航）。
 *
 *  **两套视觉，按有没有执行态事实切换**（C-4）：
 *   - 未物化 / 本轮聚合取不到：只区分**结构性事实**——锚点仓 / 普通 / 未解析。
 *     此时把执行态颜色摆上去就是拿布局假装状态。
 *   - 已物化且取到本轮聚合：节点按读模型的 `tasks[].display_status` 着色（见
 *     `EXEC_SKIN`）。着色是皮肤，状态映射唯一实现仍在读模型。
 *
 *  布局＝**批次泳道**（设计裁决，不做力导向）：列 = `batch_index`，列内节点垂直排布，
 *  边只画 `depends_on`。列序直接就是执行顺序，这是自由画布给不了的信息。
 *
 *  只读＝裁决 3：无增删边 / 调批次交互。改动动线是「回到分档审批重新生成计划」。
 *
 *  红线：state/phase 一类状态映射唯一实现在读模型，本面不派生任何状态；下列配色
 *  全部取 `index.css` 既有令牌（cream/kraft/paper-ink/paper-dim/amber/salmon/line），
 *  不新增颜色语义。 */

/* ── 泳道几何（单位 px，SVG 与 HTML 覆盖层共用同一套坐标） ───────────────── */
const NODE_W = 168;
const NODE_H = 42;
const COL_GAP = 62; // 列间距要装得下箭头，太窄会让边看起来贴在节点上
const ROW_GAP = 14;
const PAD = 14;
const HEAD_H = 20; // 批次标题行

const nodeX = (col: number) => PAD + col * (NODE_W + COL_GAP);
const nodeY = (row: number) => PAD + HEAD_H + row * (NODE_H + ROW_GAP);

/** 节点的稳定标识＝`name + batch_index`。**不能用 `repository_id`**：契约 §5.4
 *  勘正后它可为 null（catalog 无此名 / issue 域外重名歧义），多个未解析节点会
 *  塌到同一个 key 上。 */
const nodeKey = (node: { name: string; batch_index: number }) => `${node.batch_index}:${node.name}`;

interface Placed {
  node: RepositoryPlanView["dag"]["nodes"][number];
  col: number;
  row: number;
}

/* ── 执行态皮肤（C-4）─────────────────────────────────────────────────────── */

/** **展示皮肤，不是状态映射。**
 *
 *  状态映射唯一实现在读模型：契约 v0.1 §5.1 把后端 7 态算成展示 6 态
 *  `display_status`，那张表在服务端。本表只把**已经给出的那 6 个字面值**分到皮肤上，
 *  不参与任何判定——这里读不到 `backend_status`，也读不到 rework 链，想改判也无从
 *  改起，这是有意的。
 *
 *  分桶取自设计定稿 ③「橄榄绿 = 已交付 / 琥珀 = 进行中 / 弱灰 = 等待」：
 *
 *  | display_status | 桶     | 皮肤   | 归入理由 |
 *  | -------------- | ------ | ------ | -------- |
 *  | succeeded      | 已交付 | 橄榄绿 | 终态且成功 |
 *  | running        | 进行中 | 琥珀   | 在跑 |
 *  | repairing      | 进行中 | 琥珀   | 在跑（带未终态 rework 链），仍是「有人在动它」 |
 *  | pending        | 等待   | 弱灰   | 已派未起 |
 *  | blocked        | 等待   | 弱灰   | 卡在前置上，同样是「还没往前走」；§5.1 明写它不并入 repairing，故不给琥珀 |
 *  | failed         | 失败   | 赭红   | **定稿的三桶里没有失败的位置**，而 §5.1 明写 failed 是六态之一——塞进任何一桶都是谎报，故用本仓既有的失败语义色 |
 *
 *  第四桶的赭红是**实线**，与「未解析」节点的赭红**虚线**在形状上分得开；两者都不是
 *  新增颜色语义（PHASE_SKIN.failed 早就是赭红）。
 *
 *  Record 收窄到契约枚举：读模型将来多出第 7 个展示态时，这里缺项即编译错误，
 *  不会静默落到某一桶里。
 *
 *  底色一律不透明并混进 cream：节点盖在边的图层之上，透明底会让跨列的边从节点文字
 *  中间穿过去（与既有三视觉同一条约束）。 */
const EXEC_SKIN: Record<TaskDisplayStatus, string> = {
  succeeded: "border-olive bg-[color-mix(in_oklab,var(--color-olive)_20%,var(--color-cream))] text-paper-ink",
  running: "border-amber bg-[color-mix(in_oklab,var(--color-amber)_22%,var(--color-cream))] text-paper-ink",
  repairing: "border-amber bg-[color-mix(in_oklab,var(--color-amber)_22%,var(--color-cream))] text-paper-ink",
  pending: "border-paper-dim/40 bg-cream text-paper-dim",
  blocked: "border-paper-dim/40 bg-cream text-paper-dim",
  failed: "border-salmon bg-[color-mix(in_oklab,var(--color-salmon)_16%,var(--color-cream))] text-paper-ink",
};

/** 按 `batch_index` 分列。列取自节点自身而非 `execution_batches`——两者是同一份
 *  投影（服务端遍历 execution_batches 生成节点，batch_index 就是那个下标），
 *  从节点走一遍能同时拿到 `repository_id` 与 `is_focus`，不必两处对齐。
 *  列号用**批次值排序后的名次**，这样即便某个 batch_index 空缺也不会留出空列。 */
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

function NodeBox({ placed, execution }: { placed: Placed; execution: DagExecutionView | null }) {
  const { node } = placed;
  const unresolved = node.repository_id === null;

  /** 本仓在本轮的执行态。三种「没有」互不相同，压成一个会撒谎：
   *   - `execution === null`：未物化 / 本轮聚合没取到——**无事实可着色**；
   *   - 本轮没有这个仓的任务（计数 0）：计划里有它，执行面还没有它；
   *   - 有多条任务且态不一致（值为 null）：读模型没有给出仓级结论，不挑一条充数。 */
  const taskCount = execution && node.repository_id ? (execution.taskCountByRepository[node.repository_id] ?? 0) : 0;
  const status = execution && node.repository_id ? (execution.byRepository[node.repository_id] ?? null) : null;
  const colored = !unresolved && status !== null;

  // 未解析永远是虚线赭红：它没有 repository_id，也就没有任何执行态事实可谈。
  // 其余节点：有执行态就走 EXEC_SKIN，没有就退回结构三视觉（锚点仓 / 普通）。
  const skin = unresolved
    ? "border-dashed border-salmon bg-cream text-salmon"
    : colored
      ? EXEC_SKIN[status]
      : node.is_focus
        ? "border-amber bg-[color-mix(in_oklab,var(--color-amber)_15%,var(--color-cream))] text-paper-ink"
        : "border-paper-dim/60 bg-cream text-paper-ink";

  const title = unresolved
    ? `${node.name}：catalog 中查无此仓库——名字未注册，或在本 issue 域外重名歧义（域内优先后仍无唯一解），服务端不猜。`
    : colored
      ? `${node.name} · 本轮任务展示态 ${status}（读模型 §5.1 算出的 display_status，界面只上色）`
      : node.name;

  return (
    <div
      className={`absolute flex flex-col justify-center overflow-hidden rounded-hard border px-2.5 ${skin}`}
      style={{ left: nodeX(placed.col), top: nodeY(placed.row), width: NODE_W, height: NODE_H }}
      title={title}
    >
      <div className="truncate font-mono text-[11.5px] leading-tight font-semibold">{node.name}</div>
      <div className="flex items-baseline gap-1.5 overflow-hidden font-mono text-[9.5px] tracking-[0.08em] text-paper-dim uppercase">
        <span className="flex-none">batch {node.batch_index + 1}</span>
        {/* 未解析节点不隐藏（§5.4：丢节点会让批次缺项、布局就错了），显式留痕 */}
        {unresolved && <span className="flex-none font-bold text-salmon">未解析</span>}
        {/* is_focus 只在 id 非 null 时可能为 true，故与「未解析」互斥 */}
        {node.is_focus && <span className="flex-none font-bold text-amber">锚点仓</span>}
        {/* 颜色是皮肤，**字面值才是事实**：服务端给的 display_status 原样印在节点上，
            这样读者不必反查配色表，将来加了新态也不会被静默归到某个颜色里。 */}
        {colored && <span className="truncate font-bold lowercase">{status}</span>}
        {!unresolved && execution && taskCount === 0 && (
          <span className="truncate font-bold">本轮无任务</span>
        )}
        {!unresolved && execution && taskCount > 1 && status === null && (
          <span className="truncate font-bold text-salmon">{taskCount} 任务态不一</span>
        )}
      </div>
    </div>
  );
}

function DagCanvas({ dag, execution }: { dag: RepositoryPlanView["dag"]; execution: DagExecutionView | null }) {
  const { placed, batches, rows } = layout(dag.nodes);
  const width = PAD * 2 + batches.length * NODE_W + Math.max(0, batches.length - 1) * COL_GAP;
  const gridBottom = PAD + HEAD_H + rows * NODE_H + Math.max(0, rows - 1) * ROW_GAP;

  /** 边按 `repository_id` 寻址。同一 id 理论上只出现在一个批次里；真出现重复时
   *  取先出现的那个位置——画一条到「其中一个」的线，好过整条边消失。 */
  const byId = new Map<string, Placed>();
  for (const p of placed) if (p.node.repository_id !== null && !byId.has(p.node.repository_id)) byId.set(p.node.repository_id, p);

  const resolved = dag.edges
    .map((edge) => ({ from: byId.get(edge.from_repository_id), to: byId.get(edge.to_repository_id) }))
    // 服务端保证两端已解析且都落在 nodes 内（§5.4），这里的判空只是不让任何
    // 意外形状把整面炸掉——真丢了边，页脚已声明本图不等于完整依赖图。
    .filter((e): e is { from: Placed; to: Placed } => Boolean(e.from && e.to));

  /** **跨批次边要绕行**：跨越 ≥2 列的边如果直着画，会从中间那一列的节点身上穿过去，
   *  读起来就成了「A→中间仓→C」——一条不存在的依赖凭空出现。这类边改走图底部的
   *  绕行道，多条时按槽位错开，彼此不叠成一条。 */
  const skipping = resolved.filter((e) => e.to.col - e.from.col > 1);
  const laneSlots = Math.min(skipping.length, 4);
  const laneY = (i: number) => gridBottom + 10 + (i % Math.max(1, laneSlots)) * 9;
  const height = (laneSlots > 0 ? laneY(laneSlots - 1) + 6 : gridBottom) + PAD;

  return (
    <div className="relative overflow-x-auto">
      <div className="relative" style={{ width, height }}>
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
              <path d="M0,0 L6,3 L0,6 Z" className="fill-paper-dim" />
            </marker>
          </defs>

          {/* 批次泳道底纹：列即执行顺序，给一条淡竖带把「同一批可并行」说清楚 */}
          {batches.map((batch, col) => (
            <rect
              key={batch}
              x={nodeX(col) - 6}
              y={PAD + HEAD_H - 6}
              width={NODE_W + 12}
              height={gridBottom - PAD - HEAD_H + 12}
              className="fill-paper-ink/[0.04]"
            />
          ))}

          {resolved.map(({ from, to }) => {
            const x1 = nodeX(from.col) + NODE_W;
            const y1 = nodeY(from.row) + NODE_H / 2;
            const x2 = nodeX(to.col);
            const y2 = nodeY(to.row) + NODE_H / 2;
            const key = `${nodeKey(from.node)}->${nodeKey(to.node)}`;
            const span = to.col - from.col;
            const skipIndex = skipping.findIndex((e) => e.from === from && e.to === to);
            let d: string;
            if (span > 1) {
              // 绕行道：右出 → 下沉到底部车道 → 横穿 → 抬回目标左侧
              const lane = laneY(skipIndex < 0 ? 0 : skipIndex);
              d = `M ${x1} ${y1} C ${x1 + 24} ${y1}, ${x1 + 24} ${lane}, ${x1 + 48} ${lane} L ${x2 - 48} ${lane} C ${x2 - 24} ${lane}, ${x2 - 24} ${y2}, ${x2} ${y2}`;
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
                key={key}
                d={d}
                fill="none"
                className="stroke-paper-dim/70"
                strokeWidth={1.2}
                markerEnd="url(#plan-dag-arrow)"
              />
            );
          })}
        </svg>

        {batches.map((batch, col) => (
          <div
            key={batch}
            className="absolute font-mono text-[9.5px] font-bold tracking-[0.16em] text-paper-dim uppercase"
            style={{ left: nodeX(col), top: PAD - 4, width: NODE_W }}
          >
            批次 {batch + 1}
          </div>
        ))}

        {placed.map((p) => (
          <NodeBox key={nodeKey(p.node)} placed={p} execution={execution} />
        ))}
      </div>
    </div>
  );
}

/** 图例行。**两套图例按有没有执行态事实切换**——未物化时摆一排执行态色块，等于
 *  给一张没有执行事实的图配一本用不上的色谱，读者会以为自己在看运行状态。 */
function Legend({ execution }: { execution: DagExecutionView | null }) {
  const swatch = (className: string, label: string) => (
    <span key={label} className="flex items-center gap-1">
      <i className={`inline-block size-[9px] rounded-[1px] border ${className}`} />
      {label}
    </span>
  );

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-paper-dim/40 pt-2 font-mono text-[10px] text-paper-dim">
      {execution ? (
        <>
          <span className="font-bold tracking-[0.1em] uppercase">执行态 · {execution.roundLabel}</span>
          {swatch(EXEC_SKIN.succeeded, "已交付 succeeded")}
          {swatch(EXEC_SKIN.running, "进行中 running / repairing")}
          {swatch(EXEC_SKIN.pending, "等待 pending / blocked")}
          {swatch(EXEC_SKIN.failed, "失败 failed")}
          {swatch("border-dashed border-salmon bg-cream", "未解析（catalog 无此仓）")}
        </>
      ) : (
        <>
          <span className="font-bold tracking-[0.1em] uppercase">结构</span>
          {swatch(
            "border-amber bg-[color-mix(in_oklab,var(--color-amber)_15%,var(--color-cream))]",
            "锚点仓",
          )}
          {swatch("border-paper-dim/60 bg-cream", "计划内仓库")}
          {swatch("border-dashed border-salmon bg-cream", "未解析（catalog 无此仓）")}
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
      anchorName: string;
      /** 锚点取自发现链候选块而非 issue 拓扑（草稿 issue 的范围尚未冻结）。
       *  这不是同一件事实，页脚必须说出来：拓扑锚点意味着范围已定，候选锚点不意味着。 */
      anchorFromCandidate: boolean;
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
      <div className="microlabel flex items-baseline gap-2 pt-5 pb-2">
        计划 DAG
        <span className="text-[10px] tracking-normal text-tx3">
          批次泳道 · 只读（调整依赖或批次走「回到分档审批重新生成计划」）
        </span>
      </div>

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
        <PlanDagSheet
          plan={state.plan}
          anchorName={state.anchorName}
          anchorFromCandidate={state.anchorFromCandidate}
          execution={execution}
        />
      )}
    </>
  );
}

function PlanDagSheet({
  plan,
  anchorName,
  anchorFromCandidate,
  execution,
}: {
  plan: RepositoryPlanView;
  anchorName: string;
  anchorFromCandidate: boolean;
  execution: DagExecutionView | null;
}) {
  const unresolved = plan.dag.nodes.filter((n) => n.repository_id === null);

  return (
    <div className="rounded-hard bg-cream px-4 py-3.5 text-paper-ink">
      <div className="flex items-baseline gap-2.5 border-b-2 border-paper-ink pb-2">
        <span className="bg-paper-ink px-1.5 font-mono text-[11px] tracking-[0.14em] text-cream">PLAN DAG</span>
        {/* 数的是**节点**不是仓库：未解析节点在 catalog 里没有对应仓库，
            把它算进「N 仓」就是拿一个查无此仓的名字充数。 */}
        <span className="font-mono text-[11px] text-paper-dim">
          {plan.dag.nodes.length} 节点 · {plan.execution_batches.length} 批次 · {plan.dag.edges.length} 条依赖边
        </span>
        <span className="ml-auto font-mono text-[10.5px]">plan v{plan.plan_version}</span>
      </div>

      {plan.dag.nodes.length === 0 ? (
        // 快照在、批次为空：这是真实形态之一，说出来而不是画一张空画布
        <p className="py-3 font-mono text-[11.5px] text-paper-dim">
          本计划快照（v{plan.plan_version}）没有任何执行批次，无可绘制的节点。
        </p>
      ) : (
        <div className="pt-3">
          <DagCanvas dag={plan.dag} execution={execution} />
        </div>
      )}

      {plan.dag.nodes.length > 0 && (
        <div className="mt-2">
          <Legend execution={execution} />
        </div>
      )}

      <div className="mt-2 space-y-1 border-t border-paper-dim/40 pt-2 font-mono text-[10px] leading-[1.7] text-paper-dim">
        {/* 常量自述字段原样透出，不做分支解读（出新值前不猜） */}
        <div>
          粒度 {plan.dag.granularity} · 边来源 {plan.dag.edge_source} · 方向：箭头指向依赖方（起点先完成）
        </div>
        <div>
          锚点仓 <span className="font-bold">{anchorName}</span>：§5.4 端点是单仓作用域，本面取该仓的计划纸面；
          DAG 与批次是 issue 级、同一份，锚点只决定哪个节点带 `is_focus`。
        </div>
        {anchorFromCandidate && (
          <div>
            本 issue 的交付范围<b>尚未冻结</b>（详情的关联仓库为空），上面这个锚点取自发现链的候选块，
            不是拓扑事实——它只用来把这张 issue 级的图取回来，<b>不代表该仓已进入交付范围</b>。
            范围以「关联仓库 · 团队」区块为准。
          </div>
        )}
        {unresolved.length > 0 && (
          <div className="text-salmon">
            {unresolved.length} 个节点在 catalog 中查无仓库（{unresolved.map((n) => n.name).join("、")}）——名字未注册，
            或在本 issue 域外重名歧义（域内优先后仍无唯一解）；服务端不猜，节点按虚线如实留痕、不隐藏。
          </div>
        )}
        {execution ? (
          <div>
            节点着色取自 <b>{execution.roundLabel}</b> 交付聚合的 `tasks[].display_status`（契约 v0.1 §5.1
            的展示 6 态，读模型算好的），本面只把字面值分档上色、并把它原样印在节点上。
            <b>本页没有轮询</b>：着色随页面刷新更新，不会自己动。
          </div>
        ) : (
          <div>
            本图<b>未着执行态色</b>：issue 尚未物化（没有轮次）或本轮交付聚合没取到，
            无执行事实可着色。上面三种视觉只区分结构——锚点仓 / 计划内仓库 / 未解析。
          </div>
        )}
        {plan.dag.edges.length === 0 && (
          <div>
            本快照未投影出任何依赖边：可能是 task_dag 无 depends_on，也可能是依赖名全部无法解析而被丢弃——
            两者的区别只在服务端日志里，界面无从分辨。
          </div>
        )}
        <div>
          本图只画 depends_on 投影出的边；无法解析或落在批次之外的依赖名在服务端被丢弃、
          <b>只进服务端日志</b>，故本图不等于完整依赖图。任务级 / 接口级依赖边（graph_edges）恒空，另立项。
        </div>
      </div>
    </div>
  );
}
