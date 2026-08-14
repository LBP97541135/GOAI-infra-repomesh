import { useId } from "react";
import { Boxes } from "lucide-react";
import type { IntegratedPlan } from "./api";

const NODE_W = 150;
const NODE_H = 46;
const H_GAP = 44;
const V_GAP = 78;
const MARGIN = 36;

type Layout = {
  nodePos: Map<string, { row: number; col: number }>;
  rows: string[][];
  maxCols: number;
};

function layout(batches: string[][]): Layout {
  const rows = batches.filter((batch) => batch.length > 0);
  const nodePos = new Map<string, { row: number; col: number }>();
  rows.forEach((batch, row) => {
    batch.forEach((name, col) => nodePos.set(name, { row, col }));
  });
  const maxCols = rows.reduce((max, row) => Math.max(max, row.length), 0);
  return { nodePos, rows, maxCols };
}

export function DagGraph({ plan }: { plan: IntegratedPlan }) {
  const gradId = useId();
  const { nodePos, rows, maxCols } = layout(plan.execution_batches);

  // 收集边（去重、只保留两端都在批次里的）
  const edges: { source: string; target: string }[] = [];
  const seen = new Set<string>();
  for (const task of plan.task_dag) {
    for (const dep of task.depends_on ?? []) {
      if (nodePos.has(dep) && nodePos.has(task.repository)) {
        const key = `${dep}->${task.repository}`;
        if (!seen.has(key)) {
          seen.add(key);
          edges.push({ source: dep, target: task.repository });
        }
      }
    }
  }

  const cx = (name: string) =>
    MARGIN + (nodePos.get(name)!.col * (NODE_W + H_GAP)) + NODE_W / 2;
  const cy = (name: string) =>
    MARGIN + (nodePos.get(name)!.row * (NODE_H + V_GAP)) + NODE_H / 2;

  const width = Math.max(MARGIN * 2 + maxCols * (NODE_W + H_GAP) - H_GAP, 320);
  const height = Math.max(MARGIN * 2 + rows.length * (NODE_H + V_GAP) - V_GAP, 120);

  if (rows.length === 0) {
    return (
      <div className="empty-state">
        <Boxes size={26} />
        <h3>方案还没有执行批次</h3>
        <p>生成集成方案后，这里会展示仓库依赖流程图。</p>
      </div>
    );
  }

  const edgePath = (source: string, target: string) => {
    const sx = cx(source), sy = cy(source);
    const tx = cx(target), ty = cy(target);
    const sRow = nodePos.get(source)!.row;
    const tRow = nodePos.get(target)!.row;
    if (sRow === tRow) {
      // 同一批次：水平弧线
      const mid = (sx + tx) / 2;
      const dir = tx > sx ? 1 : -1;
      const ox = sx + dir * (NODE_W / 2);
      const ix = tx - dir * (NODE_W / 2);
      const ctrl = mid;
      return `M ${ox} ${sy} C ${ctrl} ${sy - 26}, ${ctrl} ${sy - 26}, ${ix} ${ty}`;
    }
    // 跨批次：从上到下
    const ox = sx;
    const oy = sy + NODE_H / 2;
    const iy = ty - NODE_H / 2;
    const ctrlY = (oy + iy) / 2;
    return `M ${ox} ${oy} C ${ox} ${ctrlY}, ${tx} ${ctrlY}, ${tx} ${iy}`;
  };

  const batchLabels: { row: number; label: string }[] = rows.map((_, row) => ({
    row,
    label: `批次 ${row + 1}`,
  }));

  return (
    <div className="dag-graph">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="方案依赖流程图">
        <defs>
          <marker
            id={`arrow-${gradId}`}
            markerWidth="9"
            markerHeight="9"
            refX="8"
            refY="4.5"
            orient="auto"
          >
            <path d="M0,0 L9,4.5 L0,9 Z" fill="#9aa69d" />
          </marker>
        </defs>

        {/* 批次标签 */}
        {batchLabels.map(({ row, label }) => (
          <text
            key={label}
            x={MARGIN - 14}
            y={MARGIN + row * (NODE_H + V_GAP) + NODE_H / 2}
            textAnchor="end"
            dominantBaseline="middle"
            className="dag-batch-label"
          >
            {label}
          </text>
        ))}

        {/* 边 */}
        {edges.map(({ source, target }) => (
          <path
            key={`${source}->${target}`}
            d={edgePath(source, target)}
            fill="none"
            stroke="#9aa69d"
            strokeWidth="1.6"
            markerEnd={`url(#arrow-${gradId})`}
            className="dag-edge"
          />
        ))}

        {/* 节点 */}
        {rows.map((batch, row) =>
          batch.map((name, col) => {
            const x = MARGIN + col * (NODE_W + H_GAP);
            const y = MARGIN + row * (NODE_H + V_GAP);
            const hasIncoming = edges.some((edge) => edge.target === name);
            const isRoot = !hasIncoming;
            return (
              <g key={name} className={`dag-node ${isRoot ? "root" : ""}`}>
                <rect
                  x={x}
                  y={y}
                  width={NODE_W}
                  height={NODE_H}
                  rx="9"
                  className="dag-node-rect"
                />
                <text
                  x={x + NODE_W / 2}
                  y={y + NODE_H / 2}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  className="dag-node-text"
                >
                  {name}
                </text>
                {isRoot && (
                  <text
                    x={x + NODE_W - 8}
                    y={y + 13}
                    textAnchor="end"
                    className="dag-root-tag"
                  >
                    入口
                  </text>
                )}
              </g>
            );
          }),
        )}
      </svg>
      <div className="dag-legend">
        <span><i className="legend-root" />入口仓库（无上游依赖）</span>
        <span><i className="legend-edge" />依赖方向</span>
        <span className="dag-edge-count">边 {edges.length} 条 · 节点 {rows.flat().length} 个</span>
      </div>
    </div>
  );
}
