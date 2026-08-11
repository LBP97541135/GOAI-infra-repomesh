import type { IssueDetail, IssueTeamRef, RoomListItem } from "../data/issueDetail";
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

const PHASE_BADGE: Record<string, string> = {
  contract: "border-line text-tx2",
  plan: "border-line text-tx2",
  execute: "border-bluegray text-bluegray",
  validate: "border-salmon text-salmon",
  release: "border-amber text-amber",
  delivered: "border-olive text-olive",
  failed: "border-salmon text-salmon",
  archived: "border-line text-tx2",
};

const RUNTIME_LABEL: Record<IssueTeamRef["runtime_status"], string> = {
  pending: "团队待建",
  ready: "团队就绪",
  failed: "建团失败",
};

const RUNTIME_SKIN: Record<IssueTeamRef["runtime_status"], string> = {
  pending: "border-line text-tx2",
  ready: "border-olive text-olive",
  failed: "border-salmon text-salmon",
};

function shortId(id: string | null): string {
  return id ? id.slice(0, 8) : "—";
}

function dayLabel(at: string): string {
  const m = at.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[2]}-${m[3]}` : at;
}

function RoomRow({ room, onOpen }: { room: RoomListItem; onOpen: (room: RoomListItem) => void }) {
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
          empty ? "bg-[#1d1810] text-[#6b6046]" : "bg-line text-kraft"
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
        <span className={`mt-0.5 block truncate text-[11.5px] ${empty ? "text-[#6b6046]" : "text-tx2"}`}>
          {empty ? "暂无消息" : room.last_message?.subject}
        </span>
      </span>

      {room.last_message && (
        <span className="flex-none font-mono text-[10.5px] text-[#6b6046]">
          {eventTime(room.last_message.at).slice(0, 5)}
        </span>
      )}
    </button>
  );
}

export function IssueDetailPage({
  detail,
  rooms,
  onBack,
  onOpenRoom,
  onToast,
}: {
  detail: IssueDetail;
  rooms: RoomListItem[];
  onBack: () => void;
  onOpenRoom: (room: RoomListItem) => void;
  onToast: (text: string) => void;
}) {
  const teamOf = (repositoryId: string) => detail.teams.find((t) => t.repository_id === repositoryId) ?? null;

  const meta = [
    `#${shortId(detail.issue_id)}`,
    detail.opened_by_agent_id ? `AGENT ${shortId(detail.opened_by_agent_id)} 发起于 ${dayLabel(detail.opened_at)}` : `发起人未关联 · ${dayLabel(detail.opened_at)}`,
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
          <span className={`rounded-hard border px-2 py-px text-[11px] ${PHASE_BADGE[detail.phase] ?? "border-line text-tx2"}`}>
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
      <div className="flex flex-wrap gap-2">
        {detail.repositories.map((repo) => {
          const team = teamOf(repo.repository_id);
          const skin = team ? RUNTIME_SKIN[team.runtime_status] : "border-line text-tx2";
          return (
            <span key={repo.repository_id} className={`rounded-hard border px-2 py-px font-mono text-[11px] ${skin}`}>
              {repo.name} · {team ? RUNTIME_LABEL[team.runtime_status] : "无团队"}
              {/* role_in_issue 恒 null：拓扑不记录仓库在 issue 中的角色语义（§3） */}
              {repo.role_in_issue ? ` · ${repo.role_in_issue}` : ""}
            </span>
          );
        })}
      </div>

      <div className="microlabel pt-5 pb-2">房间</div>
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
              <span className="text-[11px] text-[#6b6046]">
                无房间 · {team ? RUNTIME_LABEL[team.runtime_status] : "无团队"}
              </span>
            </div>
          );
        }

        return (
          <div key={repo.repository_id} className="mb-2.5 rounded-hard border border-line">
            <div className="flex items-baseline gap-2 border-b border-line bg-panel px-3 py-2 font-mono text-[11.5px] text-tx">
              {repo.name}
              <span className="text-[10.5px] text-[#6b6046]">{team?.agentteams_team_name ?? "团队未接入"}</span>
            </div>
            {group.map((room) => (
              <RoomRow key={room.room_id} room={room} onOpen={onOpenRoom} />
            ))}
          </div>
        );
      })}

      <div className="pt-4 text-[11.5px] text-[#6b6046]">
        决策夹（治理决策 + 审批弹窗）在房间读模型 CONS-33 落地后自 v1 迁入；当前 issue 有{" "}
        {detail.pending_decision_count} 项待决策，可在{" "}
        <a className="text-tx2 underline hover:text-amber-hi" href="#/delivery-v1">
          v1 交付控制台
        </a>{" "}
        处理。
      </div>
    </div>
  );
}
