import { useCallback, useEffect, useState } from "react";
import type { ConsoleTeamView } from "../api/contract";
import { fetchConsoleRepositories, fetchConsoleTeams, gridSourceMode } from "../api/grid";
import {
  TEAM_DECOMPOSITION_HINT,
  TEAM_DECOMPOSITION_LABEL,
  TEAM_STATUS_LABEL,
  TEAM_STATUS_SKIN,
  repositoryLabel,
  shortId,
  type RuntimePhase,
} from "../display";
import { RuntimeBadge } from "../components/RuntimeBadge";
import { ErrorPanel, LoadingLine, ProbeNote } from "../components/StatusBlocks";
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
  isTestTeam,
  phase,
  onOpenIssue,
  onOpenRoom,
}: {
  team: ConsoleTeamView;
  /** 前端 join 仓库档案得出（裁决：身份字段不进 team view）。撕档后存量测试
   *  团队失徽标不失功能——已知局限，验收标准 §D 显式接受，此处不做补偿。 */
  isTestTeam: boolean;
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
        {isTestTeam && (
          <span
            className="rounded-hard border border-amber px-2 py-px text-[11px] text-amber-hi"
            title="仓库档案 cross-repo-test-team：跨仓联调专职团队（供给侧身份，join 自仓库列表）"
            data-testid="team-test-badge"
          >
            测试团队
          </span>
        )}
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
        {/* 第三个持久化事实：谁拆解（D-2）。只在 leader 时出现——「平台拆解」是
            所有团队的常态，满屏重复它不是信息。与左边两个徽标并列而不合并，理由
            同上：建团结果、运行观测、采用结果是三件不同的事。 */}
        {team.decomposition_mode === "leader" && (
          <span
            className="rounded-hard border border-amber px-2 py-px text-[11px] text-amber-hi"
            title={TEAM_DECOMPOSITION_HINT.leader}
            data-testid="team-decomposition-mode"
          >
            {TEAM_DECOMPOSITION_LABEL.leader}
          </span>
        )}
        {workerCount && <span className="text-[11px] text-tx2">{workerCount}</span>}
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5">
        {/* role 来自读模型，不由前端从字段名推断——「谁是 leader」和「他是什么角色」
            是同一条记录里的两个值，验收要能在同一屏核对 mode 与 role。 */}
        <span
          className="rounded-hard border border-line px-2 py-px text-[11px] text-tx2"
          title={`role ${team.leader.role} · agent ${team.leader.agent_id}`}
          data-testid="team-leader-chip"
        >
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
  /** 徽标的 join 右表：仓库 → 档案。取用失败不挡团队列表——那时徽标整体不渲染
   *  （宁可少一个徽标，也不拿猜测冒充身份），列表本身照常。 */
  const [profileByRepository, setProfileByRepository] = useState<Map<string, string | null>>(
    () => new Map(),
  );
  useEffect(() => {
    let cancelled = false;
    fetchConsoleRepositories()
      .then(
        (repositories) =>
          !cancelled &&
          setProfileByRepository(
            new Map(repositories.map((repo) => [repo.repository_id, repo.capability_profile])),
          ),
      )
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="max-w-[860px]">
      <div className="flex items-baseline gap-3 border-b border-line pb-3">
        <h1 className="text-[16px] font-semibold text-cream">团队</h1>
        {rows && <span className="text-[11.5px] text-tx2">{rows.length} 个</span>}
      </div>

      {error ? (
        <ErrorPanel title="团队清单加载失败" message={error} onRetry={retry} />
      ) : rows === null ? (
        <LoadingLine />
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
              isTestTeam={
                profileByRepository.get(team.repository_id) === "cross-repo-test-team"
              }
              phase={phase}
              onOpenIssue={onOpenIssue}
              onOpenRoom={onOpenRoom}
            />
          ))}
        </div>
      )}

      <p className="pt-4 text-[11px] text-tx3">
        「建团结果」与「拆解模式」来自拓扑持久化，「运行时」是 AgentTeams Controller
        的当前观测 —— 三者是不同事实，故各占一个徽标。「Leader 自拆」只在 materialize
        采用了该仓库已绑定的外部 Repository Leader 时出现，界面不提供切换。
        <ProbeNote
          phase={phase}
          showProbing={rows !== null}
          probeError={probeError}
          sourceNote={gridSourceMode() === "live" ? "live · GET /console/teams（契约 v0.2 §4.2）" : "replay 夹具"}
        />
      </p>
    </div>
  );
}
