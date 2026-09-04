import type {
  IssueDetailView,
  IssueRepositoryRef,
  RoomListItemView,
} from "../../api/contract";

/** 工作台对话流的数据中枢（期 0）。
 *
 *  **唯一的职责边界**：把读模型（issue 详情 + 房间清单，后续再加审核 SSE 与
 *  轮次历史）映射成对话流的卡片序列。输入输出都是纯数据——组件只负责渲染，
 *  任何「这张卡该不该出现、顺序如何、文案是什么」的判断都收在这里，保证：
 *   - 判定逻辑可以被直接核对/测试，不埋在 JSX 里；
 *   - 期 2 换数据源（轮询→事件流）时只换取数，不改映射。
 *
 *  **诚实红线**（与全站同一套）：卡片只为**已经发生的事实**出现。没有计划就不画
 *  计划卡，没有轮次就不画轮次卡——空缺由阶段卡（phase）一句话说清，不摆占位块
 *  假装流程在走。 */

/** 卡片锚点 id 的统一前缀（期 4 的 DAG 节点滚动定位按它查找）。 */
export function workCardAnchor(card: WorkCard): string {
  return `work-card-${card.anchor}`;
}

export type WorkCard =
  /** 用户需求：右对齐气泡（对话流的起点）。 */
  | {
      kind: "requirement";
      anchor: "requirement";
      text: string;
      documentFilename: string | null;
      openedAt: string;
      /** AgentTeams 资源名，非人名（契约 §2）；null 显「未记录」 */
      openedByName: string | null;
    }
  /** 阶段卡：八相当前的落点 + 服务端 phase_note 原文。没有别的卡可讲时， */
  /*  它就是流程的「现在进行时」。 */
  | { kind: "phase"; anchor: "phase"; phase: IssueDetailView["phase"]; note: string; updatedAt: string }
  /** 计划卡：最新一轮 PlanSnapshot 的版本号。无快照不出现（A-4：三字段同null）。 */
  | { kind: "plan"; anchor: "plan"; planVersion: number; roundIndex: number; at: string | null }
  /** 建团卡：拓扑持久化的建团结果（历史事实，非运行态）。 */
  | {
      kind: "teams";
      anchor: "teams";
      teams: Array<{ teamId: string; name: string; repositoryName: string | null; runtimeStatus: string }>;
    }
  /** 仓库卡排：本 issue 的交付范围，chip 可点击唤起右栏。 */
  | {
      kind: "repositories";
      anchor: "repositories";
      repos: Array<IssueRepositoryRef & { roomId: string | null }>;
    }
  /** 轮次卡：每轮一张（B 定稿）。期 1 只含轮次自身字段，任务明细期 2 接。 */
  | {
      kind: "round";
      anchor: `round-${number}`;
      index: number;
      roundId: string;
      phase: IssueDetailView["phase"];
      status: string;
      planVersion: number | null;
      updatedAt: string | null;
      active: boolean;
    }
  /** 备注/空态卡：房间、范围等「契约明文的空」要一句话说出来，不留白。 */
  | { kind: "note"; anchor: `note-${string}`; text: string };

/** 房间按 kind 分类后挂回仓库：teamRoom 才是干活的地方，leaderDM 是单向汇报线。 */
function teamRoomByRepository(rooms: RoomListItemView[]): Map<string, RoomListItemView> {
  const map = new Map<string, RoomListItemView>();
  for (const room of rooms) {
    if (room.kind === "team_room") map.set(room.repository_id, room);
  }
  return map;
}

export function buildWorkStream(detail: IssueDetailView, rooms: RoomListItemView[]): WorkCard[] {
  const cards: WorkCard[] = [];

  cards.push({
    kind: "requirement",
    anchor: "requirement",
    // 需求文本理论上可空（旧数据）：空则以标题兜底，绝不渲染一张空气泡
    text: detail.requirement_text ?? detail.title,
    documentFilename: detail.document_filename,
    openedAt: detail.opened_at,
    openedByName: detail.opened_by_name,
  });

  cards.push({
    kind: "phase",
    anchor: "phase",
    phase: detail.phase,
    note: detail.phase_note,
    updatedAt: detail.updated_at,
  });

  // 计划卡：取最新一轮带快照的 plan_version（rounds 按时间序，从后往前找第一个）
  for (let i = detail.rounds.length - 1; i >= 0; i -= 1) {
    const round = detail.rounds[i];
    if (round.plan_version !== null) {
      cards.push({ kind: "plan", anchor: "plan", planVersion: round.plan_version, roundIndex: i + 1, at: round.created_at });
      break;
    }
  }

  if (detail.teams.length > 0) {
    const nameByRepositoryId = new Map(detail.repositories.map((r) => [r.repository_id, r.name]));
    cards.push({
      kind: "teams",
      anchor: "teams",
      teams: detail.teams.map((t) => ({
        teamId: t.team_id,
        name: t.agentteams_team_name,
        repositoryName: nameByRepositoryId.get(t.repository_id) ?? null,
        runtimeStatus: t.runtime_status,
      })),
    });
  }

  const roomByRepository = teamRoomByRepository(rooms);
  if (detail.repositories.length > 0) {
    cards.push({
      kind: "repositories",
      anchor: "repositories",
      repos: detail.repositories.map((r) => ({
        ...r,
        roomId: roomByRepository.get(r.repository_id)?.room_id ?? null,
      })),
    });
  }

  detail.rounds.forEach((round, i) => {
    cards.push({
      kind: "round",
      anchor: `round-${i + 1}`,
      index: i + 1,
      roundId: round.round_id,
      phase: round.phase,
      status: round.status,
      planVersion: round.plan_version,
      updatedAt: round.updated_at,
      active: round.round_id === (detail.active_round_id ?? detail.latest_round_id),
    });
  });

  if (rooms.length === 0 && detail.teams.length === 0) {
    cards.push({
      kind: "note",
      anchor: "note-no-rooms",
      text: "尚未建团：仓库房间会在计划物化后出现，届时点仓库即可在右侧打开房间面板。",
    });
  }

  return cards;
}

/** 新会话（尚未创建 issue）的工作台流：只有一张引导卡。 */
export function newSessionStream(workspaceName: string | null): WorkCard[] {
  return [
    {
      kind: "note",
      anchor: "note-new-session",
      text:
        "新会话。" +
        (workspaceName
          ? `当前工作区：${workspaceName}。`
          : "未选择工作区（可选）。") +
        "在下方输入需求（可附文档）发送即创建 issue，规划、建团、轮次会依次流进来。",
    },
  ];
}
