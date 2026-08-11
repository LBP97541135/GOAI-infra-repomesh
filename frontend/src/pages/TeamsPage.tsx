import { useCallback } from "react";
import type { ConsoleTeamView } from "../api/contract";
import { fetchConsoleTeams, gridSourceMode } from "../api/grid";
import { TEAM_STATUS_LABEL, TEAM_STATUS_SKIN, repositoryLabel, shortId, type RuntimePhase } from "../display";
import { RuntimeBadge } from "../components/RuntimeBadge";
import { useRuntimeRows } from "./useRuntimeRows";

/** 团队页（CONS-44 / 契约 v0.2 §4.2）。
 *
 *  **本页的核心约束：`runtime_status` 与 `runtime.phase` 各占一个徽标，不合并。**
 *  前者是拓扑持久化的**建团结果**（历史事实：这个团队当初建成了），后者是 Controller
 *  的**当前观测态**（可能根本打不通）。合成一个徽标的后果很具体：controller 离线时
 *  「建团就绪」会被显示成「团队故障」，而团队其实建成过、房间也还在。契约明文禁止。
 *
 *  联调环境正是这个样本：四个团队 runtime_status 全 ready，runtime 全 {reachable:false}。 */

// X3：建团三态措辞与皮肤用 display.ts 唯一表（「团队待建」为正）

function TeamCard({
  team,
  phase,
  onOpenIssue,
  onOpenRoom,
}: {
  team: ConsoleTeamView;
  phase: RuntimePhase;
  onOpenIssue: (issueId: string) => void;
  onOpenRoom: (issueId: string, roomId: string) => void;
}) {
  const rt = team.runtime;
  // ready_workers/total_workers 只有探测通了才有；否则整条不渲染，不填 0/0
  const workerCount =
    rt !== null && rt.reachable && rt.total_workers !== null
      ? `就绪 ${rt.ready_workers ?? "—"}/${rt.total_workers}`
      : null;

  return (
    <div className="rounded-hard border border-line bg-panel px-4 py-3">
      <div className="flex flex-wrap items-baseline gap-2.5">
        <span className="font-mono text-[12.5px] text-tx">{team.agentteams_team_name}</span>
        <span className="text-[11.5px] text-tx2">
          {/* catalog 查不到时为 null（§7.3 已把三处同源标注统一）——不拿 id 冒充仓库名 */}
          {repositoryLabel(team.repository_name, team.repository_id)}
        </span>
        <button
          className="ml-auto rounded-hard border border-line px-2 py-px text-[11px] text-tx2 hover:border-amber hover:text-amber-hi"
          title={`issue ${team.issue_id}`}
          onClick={() => onOpenIssue(team.issue_id)}
        >
          #{shortId(team.issue_id)}
        </button>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {/* 两个事实并排，各自带 title 说明来源——契约明文不得合并成一个 */}
        <span
          className={`rounded-hard border px-2 py-px text-[11px] ${TEAM_STATUS_SKIN[team.runtime_status]}`}
          title="拓扑持久化的建团结果（历史事实）"
        >
          {TEAM_STATUS_LABEL[team.runtime_status] ?? team.runtime_status}
        </span>
        <RuntimeBadge phase={phase} runtime={team.runtime} />
        {workerCount && <span className="text-[11px] text-tx2">{workerCount}</span>}
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5">
        <span className="rounded-hard border border-line px-2 py-px text-[11px] text-tx2">
          <span className="text-amber">LD</span> {team.leader.name ?? shortId(team.leader.agent_id)}
        </span>
        {team.workers.map((w) => (
          <span key={w.agent_id} className="rounded-hard border border-line px-2 py-px text-[11px] text-tx2">
            WK {w.name ?? shortId(w.agent_id)}
          </span>
        ))}
        {team.workers.length === 0 && <span className="text-[11px] text-tx3">尚无 worker</span>}
      </div>

      <div className="mt-2 flex flex-wrap gap-2">
        {team.team_room_id ? (
          <button
            className="font-mono text-[11px] text-tx2 hover:text-amber-hi"
            onClick={() => onOpenRoom(team.issue_id, team.team_room_id!)}
          >
            ▤ teamRoom
          </button>
        ) : (
          <span className="text-[11px] text-tx3">teamRoom 未建</span>
        )}
        {team.leader_room_id ? (
          <button
            className="font-mono text-[11px] text-tx2 hover:text-amber-hi"
            onClick={() => onOpenRoom(team.issue_id, team.leader_room_id!)}
          >
            ▤ leaderDM
          </button>
        ) : (
          <span className="text-[11px] text-tx3">leaderDM 未建</span>
        )}
      </div>
    </div>
  );
}

export function TeamsPage({
  onOpenIssue,
  onOpenRoom,
}: {
  onOpenIssue: (issueId: string) => void;
  onOpenRoom: (issueId: string, roomId: string) => void;
}) {
  const fetcher = useCallback((withRuntime: boolean) => fetchConsoleTeams(withRuntime), []);
  const { rows, error, phase, probeError, retry } = useRuntimeRows<ConsoleTeamView>(fetcher);

  return (
    <div className="max-w-[860px]">
      <div className="flex items-baseline gap-3 border-b border-line pb-3">
        <h1 className="text-[16px] font-semibold text-cream">团队</h1>
        {rows && <span className="text-[11.5px] text-tx2">{rows.length} 个</span>}
      </div>

      {error ? (
        <div className="mt-4 rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3">
          <div className="eyebrow mb-1 text-salmon">团队清单加载失败</div>
          <p className="text-[12px] text-salmon">{error}</p>
          <button
            className="mt-2 rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
            onClick={retry}
          >
            重试
          </button>
        </div>
      ) : rows === null ? (
        <p className="py-8 text-center text-[12.5px] text-tx2">加载中…</p>
      ) : rows.length === 0 ? (
        <div className="py-8 text-center text-[12.5px] text-tx3">
          还没有任何团队 · 团队在 issue 范围确认时按「issue × 仓库」组建
        </div>
      ) : (
        <div className="mt-4 grid gap-2">
          {rows.map((team) => (
            <TeamCard
              key={team.team_id}
              team={team}
              phase={phase}
              onOpenIssue={onOpenIssue}
              onOpenRoom={onOpenRoom}
            />
          ))}
        </div>
      )}

      <p className="pt-4 text-[11px] text-tx3">
        「建团结果」来自拓扑持久化，「运行时」是 AgentTeams Controller 的当前观测 ——
        两者是不同事实，故分列两个徽标。
        {phase === "loading" && rows !== null && " 运行时探测进行中（首屏不等它）。"}
        {phase === "failed" && probeError && ` 运行时探测请求失败：${probeError.slice(0, 60)}`}
        {" "}数据源：{gridSourceMode() === "live" ? "live · GET /console/teams（契约 v0.2 §4.2）" : "replay 夹具"}
      </p>
    </div>
  );
}
