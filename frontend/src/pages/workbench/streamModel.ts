import type {
  DeliveryAggregate,
  DeliveryTaskView,
  IssueDetailView,
  IssueRepositoryRef,
  RoomListItemView,
} from "../../api/contract";
import type { Decision } from "../../types";
import { approvalForDecision } from "../../viewmodel";

/** 工作台对话流的数据中枢（期 0 建，期 2 扩）。
 *
 *  **唯一的职责边界**：把读模型（issue 详情 + 房间清单 + 轮次任务明细 + 活跃轮
 *  决策夹）映射成对话流的卡片序列。输入输出都是纯数据——组件只负责渲染，任何
 *  「这张卡该不该出现、顺序如何、文案是什么」的判断都收在这里，保证：
 *   - 判定逻辑可以被直接核对/测试，不埋在 JSX 里；
 *   - 后续换数据源（轮询→事件流）时只换取数，不改映射。
 *
 *  **诚实红线**（与全站同一套）：卡片只为**已经发生的事实**出现。没有计划就不画
 *  计划卡，某轮取不到任务明细就不摆假进度；状态一律透传读模型原值
 *  （`display_status` / phase_note），前端不翻译不派生。 */

/** 卡片锚点 id 的统一前缀（期 4 的 DAG 节点滚动定位按它查找）。 */
export function workCardAnchor(card: WorkCard): string {
  return `work-card-${card.anchor}`;
}

/** 轮次任务明细的展示行：DeliveryTaskView 的窄化，只留对话流要说的字段。 */
export interface RoundTaskRow {
  taskId: string;
  title: string;
  /** 读模型 §5.1 展示态原值，透传不翻译 */
  displayStatus: string;
  agent: string | null;
  attempt: number;
  /** null = 从未派工（契约 §8.7.4：这是这句话本身，不是缺数据） */
  lastDispatchedAt: string | null;
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
  /** 轮次卡：每轮一张（B 定稿），卡内任务逐行原地更新（tick 按 display_status）。 */
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
      /** 该轮任务明细；取用失败/夹具未覆盖时为 null——如实不摆假进度 */
      tasks: RoundTaskRow[] | null;
    }
  /** 审批卡（B 定稿：流内直接操作）。来自活跃轮的决策夹；批准/查看证据的
   *  写回路在页面层（授权单按点击的卡构建，S1）。 */
  | {
      kind: "decision";
      anchor: `decision-${string}`;
      decisionId: string;
      /** 契约 §4.3 仅此两类 */
      decisionKind: "approve" | "watch";
      title: string;
      body: string;
      /** 授权单构建（approvalForDecision）与证据切片都认这个 id */
      repositoryId: string | null;
      repositoryName: string | null;
      headSha: string | null;
      /** A-18：该决策指向仓库里 agent 自述「未验证」的任务数（0 = 无此声明） */
      unverifiedCount: number;
      roundIndex: number;
    }
  /** 备注/空态卡：房间、范围等「契约明文的空」要一句话说出来，不留白。 */
  | { kind: "note"; anchor: `note-${string}`; text: string };

/** buildWorkStream 的输入：详情与房间之外，期 2 增加轮次任务与活跃轮决策夹。 */
export interface WorkStreamInput {
  detail: IssueDetailView;
  rooms: RoomListItemView[];
  /** 每轮任务原文，按 round_id 归桶（fetchRoundDecisionHistory().tasks） */
  tasksByRound: Record<string, DeliveryTaskView[]>;
  /** 活跃轮（active ?? latest）的决策夹；null = 无轮次或取用失败 */
  activeDeck: { roundId: string; roundIndex: number; decisions: Decision[]; aggregate: DeliveryAggregate } | null;
}

function taskRows(tasks: DeliveryTaskView[] | undefined): RoundTaskRow[] | null {
  if (!tasks) return null;
  return tasks.map((t) => ({
    taskId: t.task_id,
    title: t.title,
    displayStatus: t.display_status,
    agent: t.agent,
    attempt: t.attempt,
    lastDispatchedAt: t.last_dispatched_at,
  }));
}

/** 房间按 kind 分类后挂回仓库：teamRoom 才是干活的地方，leaderDM 是单向汇报线。 */
function teamRoomByRepository(rooms: RoomListItemView[]): Map<string, RoomListItemView> {
  const map = new Map<string, RoomListItemView>();
  for (const room of rooms) {
    if (room.kind === "team_room") map.set(room.repository_id, room);
  }
  return map;
}

/** 需求文本里「用户手打的话」与「附件文档解析全文」的分界（U+2063 不可见分隔符，
 *  正常输入不可能出现）。为什么要有它：契约里文档解析文本只能随 `requirement_text`
 *  交给规划（创建议题没有独立的文档字段），但对话流不该把文档内容当用户的话贴
 *  出来——用户没打字就一个字都不展示（气泡只留文件卡）。
 *  无标记 = 旧数据或纯手输，整段照旧算用户的话。 */
const DOC_SENTINEL = "\u2063";

/** 发送侧唯一实现：手打文字在前，分界符，文档解析全文在后（规划两段都要读）。 */
export function composeRequirementText(typed: string, documentText: string): string {
  return typed ? `${typed}\n\n${DOC_SENTINEL}\n${documentText}` : `${DOC_SENTINEL}\n${documentText}`;
}

/** 展示侧唯一实现：分界符之前的才是用户自己的话；trim 后为空返回 null——
 *  调用方据此只留文件卡，不擅自复述文档内容。 */
export function typedRequirementText(text: string | null): string | null {
  if (!text) return null;
  const cut = text.indexOf(DOC_SENTINEL);
  const typed = (cut === -1 ? text : text.slice(0, cut)).trim();
  return typed || null;
}

export function buildWorkStream(input: WorkStreamInput): WorkCard[] {
  const { detail, rooms, tasksByRound, activeDeck } = input;
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
    const roundIndex = i + 1;
    cards.push({
      kind: "round",
      anchor: `round-${roundIndex}`,
      index: roundIndex,
      roundId: round.round_id,
      phase: round.phase,
      status: round.status,
      planVersion: round.plan_version,
      updatedAt: round.updated_at,
      active: round.round_id === (detail.active_round_id ?? detail.latest_round_id),
      tasks: taskRows(tasksByRound[round.round_id]),
    });

    // 决策是轮次粒度：审批卡紧跟在它所属的那一轮卡片后面（B 定稿的流内操作）
    if (activeDeck && activeDeck.roundId === round.round_id) {
      const repoNameById = new Map(activeDeck.aggregate.repositories.map((r) => [r.repository_id, r.name]));
      for (const decision of activeDeck.decisions) {
        cards.push({
          kind: "decision",
          anchor: `decision-${decision.id}`,
          decisionId: decision.id,
          decisionKind: decision.kind,
          title: decision.title,
          body: decision.body,
          repositoryId: decision.repositoryId,
          repositoryName: decision.repositoryId
            ? repoNameById.get(decision.repositoryId) ?? null
            : null,
          headSha: decision.headSha,
          unverifiedCount: decision.repositoryId
            ? approvalForDecision(activeDeck.aggregate, decision)?.unverified.length ?? 0
            : 0,
          roundIndex,
        });
      }
    }
  });

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
          : "作用范围：全部工作区（可在侧栏切换）。") +
        "在下方输入需求（可附文档）发送即创建 issue，规划、建团、轮次会依次流进来。",
    },
  ];
}
