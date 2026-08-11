import type { IssueDetailView, IssueRoundView, RoomListItemView } from "../api/contract";
import type { Decision } from "../types";
import { DecisionDeck } from "../components/DecisionDeck";
import { RoundsPanel, type RoundHistoryState } from "../components/RoundsPanel";
import { PHASE_SKIN, PHASE_SKIN_FALLBACK, TEAM_STATUS_LABEL, TEAM_STATUS_SKIN, dayLabel, openedBy, shortId } from "../display";
import { eventTime } from "../viewmodel";

/** issue 详情页（CONS-42）。版式按原型 redesign-issue-centric.html 的 `#v-detail`：
 *  标题+元数据+徽标 → 原始需求卡 → 关联仓库·团队芯片 → 房间区（每仓 teamRoom + leaderDM）。
 *
 *  红线：state / phase / phase_note / runtime_status / live 全部由读模型派生，本页只渲染。
 *  下列配色是展示皮肤，取自 Variant D 既有令牌，不是状态映射。
 *
 *  与原型的三处诚实偏差（原型是假数据，live 无源）：
 *   1. 原型的 `#42` 编号 → issue_id 短版（`issue_key` 恒 null，§0/§6.1）；
 *   2. 原型的「王倩 发起于」→ agent 短版（只有 `opened_by_agent_id`，无人名）；
 *   3. 原型的「契约 c3f1a29e」→ 契约投影无 hash 字段，改显 specification 短版 + 版本。 */

// X2/X3：八相皮肤与建团三态措辞均用 display.ts 唯一表

function RoomRow({ room, onOpen }: { room: RoomListItemView; onOpen: (room: RoomListItemView) => void }) {
  const empty = room.last_message === null;
  const tag = room.kind === "team_room" ? "TR" : "DM";

  return (
    <button
      className={`flex w-full items-center gap-2.5 border-b border-panel px-3 py-2.5 text-left last:border-b-0 hover:bg-[#241e13] ${
        room.live ? "bg-[#1a160e]" : ""
      }`}
      onClick={() => onOpen(room)}
    >
      <span
        className={`grid size-8 flex-none place-items-center rounded-hard font-mono text-[11px] ${
          empty ? "bg-[#1d1810] text-tx3" : "bg-line text-kraft"
        }`}
      >
        {tag}
      </span>

      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className={`text-[12.5px] ${empty ? "text-tx2" : "text-tx"}`}>
            {room.kind === "team_room" ? "teamRoom" : "leaderDM"}
          </span>
          <span className="text-[10.5px] text-tx2">
            {room.members.length} 成员
            {!room.live && !empty ? " · 静默" : ""}
          </span>
          {room.live && (
            <span
              className="rounded-hard border border-salmon px-1.5 font-mono text-[10px] tracking-[0.1em] text-salmon"
              title="LIVE 由该仓在途任务派生，不是 Matrix presence（契约 v0.2 §5.3）"
            >
              <i className="blink mr-1 inline-block size-[5px] rounded-full bg-salmon align-middle not-italic" />
              LIVE
            </span>
          )}
        </span>
        {/* 空房间不装填占位消息（§5.1） */}
        <span className={`mt-0.5 block truncate text-[11.5px] ${empty ? "text-tx3" : "text-tx2"}`}>
          {empty ? "暂无消息" : room.last_message?.subject}
        </span>
      </span>

      {room.last_message && (
        <span className="flex-none font-mono text-[10.5px] text-tx3">
          {eventTime(room.last_message.at).slice(0, 5)}
        </span>
      )}
    </button>
  );
}

export function IssueDetailPage({
  detail,
  rooms,
  deck,
  deckHidden,
  deckNote,
  onToggleDeck,
  onBringToFront,
  onDecisionAction,
  onBack,
  onOpenRoom,
  onToast,
  roundsExpanded,
  roundsHistory,
  onToggleRound,
  archiveConfirmId,
  archivingId,
  onArchiveRound,
}: {
  detail: IssueDetailView;
  rooms: RoomListItemView[];
  deck: Decision[];
  deckHidden: boolean;
  /** 决策夹的轮次与数据源说明；null = 该 issue 无轮次，整块不渲染 */
  deckNote: string | null;
  onToggleDeck: () => void;
  onBringToFront: (id: string) => void;
  onDecisionAction: (decision: Decision, actionIdx: number) => void;
  onBack: () => void;
  onOpenRoom: (room: RoomListItemView) => void;
  onToast: (text: string) => void;
  /** 轮次索引与跨轮决策（B-6）：展开态与取数结果由容器持有 */
  roundsExpanded: Record<string, boolean>;
  roundsHistory: Record<string, RoundHistoryState>;
  onToggleRound: (round: IssueRoundView) => void;
  /** 轮次归档（B-4）：两步确认态与在途态由容器持有 */
  archiveConfirmId: string | null;
  archivingId: string | null;
  onArchiveRound: (round: IssueRoundView) => void;
}) {
  const teamOf = (repositoryId: string) => detail.teams.find((t) => t.repository_id === repositoryId) ?? null;

  const meta = [
    `#${shortId(detail.issue_id)}`,
    `${openedBy(detail)} 发起于 ${dayLabel(detail.opened_at)}`,
    `第 ${detail.round_count} 轮交付`,
    `${detail.repository_count} 仓`,
  ].join(" · ");

  return (
    <div className="max-w-[860px]">
      <button className="pb-3 text-[11.5px] text-tx2 hover:text-tx" onClick={onBack}>
        ‹ issue
      </button>

      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-[17px] leading-[1.4] font-semibold text-tx">{detail.title}</h1>
          <div className="mt-1.5 font-mono text-[11.5px] text-tx2">{meta}</div>
        </div>

        <div className="mt-1 flex flex-none flex-wrap justify-end gap-1.5">
          {/* state 与 phase_note 均由读模型派生（§2.1/§2.2） */}
          <span className="rounded-hard bg-amber px-2 py-px text-[11px] font-semibold text-[#191308]">
            {detail.state === "open" ? "Open" : "Closed"} · {detail.phase_note}
          </span>
          {/* §2.1：paused 不影响 state，必须独立徽标呈现 */}
          {detail.operational_status === "paused" && (
            <span className="rounded-hard border border-salmon px-2 py-px text-[11px] text-salmon">已暂停</span>
          )}
          {detail.operational_status === "cancelled" && (
            <span className="rounded-hard border border-line px-2 py-px text-[11px] text-tx2">已取消</span>
          )}
          <span className={`rounded-hard border px-2 py-px text-[11px] ${PHASE_SKIN[detail.phase]?.badge ?? PHASE_SKIN_FALLBACK.badge}`}>
            {detail.phase}
          </span>
        </div>
      </div>

      <div className="mt-3.5 rounded-hard border border-line bg-panel px-3.5 py-3">
        <div className="microlabel pb-2">
          原始需求
          {detail.contract
            ? ` · 工程契约 ${shortId(detail.contract.specification_id)} · v${detail.contract.version} · ${detail.contract.status}`
            : " · 工程契约未接入"}
        </div>
        <p className="text-[12.5px] leading-[1.7] text-kraft">
          {detail.requirement_text ?? "需求文本未接入（无 Project 注册表，取自最早 PlanSnapshot）"}
        </p>
      </div>

      {detail.required_checkpoints.length > 0 && (
        <button
          className="mt-2 text-[11.5px] text-tx2 hover:text-amber-hi"
          onClick={() =>
            onToast("人工检查点走 main 既有审核台（/review-requests）；v0.2 决策夹只含治理决策")
          }
        >
          本 issue 设有人工检查点：{detail.required_checkpoints.join(" · ")} ›
        </button>
      )}

      <div className="microlabel pt-4 pb-2">关联仓库 · 团队</div>
      {/* 空要说出来，不能让区块凭空消失——草稿 issue 尚未确定范围就是这个形态 */}
      {detail.repositories.length === 0 && (
        <p className="text-[12px] text-tx3">尚未确定交付范围（范围由 Org Leader 提议、各仓 Leader 评审后冻结）。</p>
      )}
      <div className="flex flex-wrap gap-2">
        {detail.repositories.map((repo) => {
          const team = teamOf(repo.repository_id);
          const skin = team ? TEAM_STATUS_SKIN[team.runtime_status] : "border-line text-tx2";
          return (
            <span key={repo.repository_id} className={`rounded-hard border px-2 py-px font-mono text-[11px] ${skin}`}>
              {repo.name} · {team ? TEAM_STATUS_LABEL[team.runtime_status] : "无团队"}
              {/* role_in_issue 恒 null：拓扑不记录仓库在 issue 中的角色语义（§3） */}
              {repo.role_in_issue ? ` · ${repo.role_in_issue}` : ""}
            </span>
          );
        })}
      </div>

      {/* 决策夹：位置按设计定稿——关联仓库芯片之后、房间区之前。
          决策是轮次粒度，deckNote 说明取的是哪一轮，避免与 issue 级
          pending_decision_count（跨轮求和）被读成同一个数。 */}
      {(deck.length > 0 || deckNote) && (
        <>
          <div className="microlabel flex items-baseline gap-2 pt-5 pb-2">
            决策夹
            {deckNote && <span className="text-[10px] tracking-normal text-tx3">{deckNote}</span>}
          </div>
          {deck.length > 0 ? (
            <DecisionDeck
              deck={deck}
              hidden={deckHidden}
              onToggleHidden={onToggleDeck}
              onBringToFront={onBringToFront}
              onAction={onDecisionAction}
            />
          ) : (
            <p className="text-[12px] text-tx3">本轮无待决策事项。</p>
          )}
        </>
      )}

      {/* 轮次索引 + 跨轮决策（B-6）：位置在决策夹（当前轮）之后、房间区之前 */}
      <RoundsPanel
        detail={detail}
        currentRoundId={detail.active_round_id ?? detail.latest_round_id}
        expanded={roundsExpanded}
        history={roundsHistory}
        archiveConfirmId={archiveConfirmId}
        archivingId={archivingId}
        onToggleRound={onToggleRound}
        onArchiveRound={onArchiveRound}
      />

      <div className="microlabel pt-5 pb-2">房间</div>
      {rooms.length === 0 && (
        <p className="text-[12px] text-tx3">
          {detail.repositories.length === 0
            ? "尚未建团，暂无房间。团队在范围确认时按「issue × 仓库」自动组建（每团队 teamRoom + leaderDM 双房间）。"
            : "本 issue 的仓库均尚未建团，暂无房间。"}
        </p>
      )}
      {detail.repositories.map((repo) => {
        const team = teamOf(repo.repository_id);
        const group = rooms.filter((r) => r.repository_id === repo.repository_id);

        if (group.length === 0) {
          return (
            <div
              key={repo.repository_id}
              className="mb-2 flex items-baseline gap-2 rounded-hard border border-line px-3 py-2"
            >
              <span className="font-mono text-[11.5px] text-tx2">{repo.name}</span>
              <span className="text-[11px] text-tx3">
                无房间 · {team ? TEAM_STATUS_LABEL[team.runtime_status] : "无团队"}
              </span>
            </div>
          );
        }

        return (
          <div key={repo.repository_id} className="mb-2.5 rounded-hard border border-line">
            <div className="flex items-baseline gap-2 border-b border-line bg-panel px-3 py-2 font-mono text-[11.5px] text-tx">
              {repo.name}
              <span className="text-[10.5px] text-tx3">{team?.agentteams_team_name ?? "团队未接入"}</span>
            </div>
            {group.map((room) => (
              <RoomRow key={room.room_id} room={room} onOpen={onOpenRoom} />
            ))}
          </div>
        );
      })}

      {/* pending_decision_count 是跨轮求和（§2），决策夹只呈现当前一轮：两个数不等时
          说清楚差在哪，别让人以为决策夹漏了事项。查看入口 = 上方轮次区逐轮展开（B-6）。 */}
      {detail.pending_decision_count > deck.length && (
        <div className="pt-4 text-[11.5px] text-tx3">
          该 issue 跨全部 {detail.round_count} 轮共 {detail.pending_decision_count} 项待决策，决策夹只显示当前一轮的{" "}
          {deck.length} 项——其余轮次在上方「轮次」区逐轮展开查看（批准动作仍只在当前轮的决策夹）。
        </div>
      )}
    </div>
  );
}
