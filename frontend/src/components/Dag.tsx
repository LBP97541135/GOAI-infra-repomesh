import { repos, tasks } from "../data/mock";
import type { TaskStatus } from "../types";

/** 四泳道 DAG。结构对齐 frontend-prototype/dag.js，皮肤按视图切换：
 *  room = 暗色房间流，paper = 计划纸面。 */

interface StatusColors {
  stroke: string;
  fill: string;
}

interface DagSkin {
  box: string;
  lane: string;
  laneLabel: string;
  edge: string;
  edgeActive: string;
  title: string;
  id: string;
  status: Record<TaskStatus, StatusColors>;
}

const SKINS: Record<"room" | "paper", DagSkin> = {
  room: {
    box: "#262013",
    lane: "#453c28",
    laneLabel: "#c99e52",
    edge: "#5a4d33",
    edgeActive: "#8a7648",
    title: "#e9dec2",
    id: "#93876c",
    status: {
      succeeded: { stroke: "#94a35a", fill: "#94a35a" },
      repairing: { stroke: "#c99e52", fill: "#e3c078" },
      blocked: { stroke: "#c26d51", fill: "#e8a184" },
      running: { stroke: "#8aa0b4", fill: "#8aa0b4" },
      pending: { stroke: "#6b6046", fill: "#6b6046" },
      failed: { stroke: "#c26d51", fill: "#c26d51" },
    },
  },
  paper: {
    box: "#f2ead4",
    lane: "#b3a67f",
    laneLabel: "#6d6349",
    edge: "#8a7c5c",
    edgeActive: "#4a4128",
    title: "#221c10",
    id: "#6d6349",
    status: {
      succeeded: { stroke: "#5b6d2c", fill: "#5b6d2c" },
      repairing: { stroke: "#a3702e", fill: "#a3702e" },
      blocked: { stroke: "#a34a2e", fill: "#a34a2e" },
      running: { stroke: "#3d617a", fill: "#3d617a" },
      pending: { stroke: "#8a7f63", fill: "#8a7f63" },
      failed: { stroke: "#a34a2e", fill: "#a34a2e" },
    },
  },
};

const STATUS_LABEL: Record<TaskStatus, string> = {
  succeeded: "已完成",
  running: "执行中",
  repairing: "修复中 2/3",
  blocked: "受阻",
  pending: "等待依赖",
  failed: "失败",
};

const X = (col: number) => 150 + col * 285;
const Y = (lane: number) => 52 + lane * 72;
const W = 235;
const H = 54;

export function Dag({ skin: skinName }: { skin: "room" | "paper" }) {
  const skin = SKINS[skinName];
  const byId = Object.fromEntries(tasks.map((t) => [t.id, t]));

  return (
    <svg className="block h-auto w-full" viewBox="0 0 980 322" role="img" aria-label="跨仓任务 DAG">
      {repos.map((r, i) => (
        <g key={r.id}>
          <line
            x1={18}
            y1={Y(i)}
            x2={962}
            y2={Y(i)}
            stroke={skin.lane}
            strokeWidth={1}
            strokeDasharray="2 6"
          />
          <text x={18} y={Y(i) - 14} fill={skin.laneLabel} className="font-mono text-[11px]">
            {r.id}
          </text>
        </g>
      ))}
      {tasks.flatMap((t) =>
        (t.deps ?? []).map((dep) => {
          const s = byId[dep];
          const x1 = X(s.col) + W;
          const y1 = Y(s.lane);
          const x2 = X(t.col);
          const y2 = Y(t.lane);
          const mx = (x1 + x2) / 2;
          const active = s.status === "succeeded" && t.status !== "pending";
          return (
            <path
              key={`${dep}-${t.id}`}
              d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
              fill="none"
              stroke={active ? skin.edgeActive : skin.edge}
              strokeWidth={1.5}
            />
          );
        }),
      )}
      {tasks.map((t) => {
        const c = skin.status[t.status];
        return (
          <g key={t.id} transform={`translate(${X(t.col)}, ${Y(t.lane) - H / 2})`}>
            <rect
              width={W}
              height={H}
              rx={8}
              fill={skin.box}
              stroke={c.stroke}
              strokeWidth={1.5}
              strokeDasharray={t.status === "repairing" ? "5 3" : t.status === "pending" ? "3 4" : undefined}
              opacity={t.status === "pending" ? 0.75 : 1}
            />
            <circle cx={18} cy={H / 2} r={5} fill={c.fill} className={t.status === "running" ? "blink" : undefined} />
            <text x={34} y={22} fill={skin.id} className="font-mono text-[11px]">
              {t.id} · {t.agent}
            </text>
            <text x={34} y={40} fill={skin.title} className="text-[12px]">
              {t.title}
            </text>
            <text x={W - 10} y={22} textAnchor="end" fill={c.fill} className="text-[11px] font-semibold">
              {STATUS_LABEL[t.status]}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
