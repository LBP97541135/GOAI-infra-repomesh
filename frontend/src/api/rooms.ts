/** issue 详情 / 房间数据源：live | replay，开关沿用 `resolveDataSourceMode()`。
 *  live 打契约 v0.2 §3 / §5.1 / §5.2 / §5.4；replay 走本地夹具。两侧同一契约类型。 */
import type {
  DeliveryEventKind,
  DeliveryEventsPage,
  IssueDetailView,
  RepositoryPlanView,
  RoomListItemView,
  RoomStreamPage,
} from "./contract";
import type { RepositoryEnv } from "../types";
import { createApiClient } from "./client";
import { resolveDataSourceMode } from "./source";
import { repositoryEnvFromAggregate } from "../viewmodel";
import { aggregate as replayAggregate } from "../data/replay";
import {
  issueDetailFixture,
  repositoryPlanFixture,
  roomStreamFixtures,
  roomsFixture,
  roundEventsFixture,
} from "../data/issueDetail";

/** 房间流单页条数：种子每房间 0-5 条，取 50 足够；真实规模由 next_cursor 续读。 */
export const ROOM_STREAM_LIMIT = 50;

/** 轮询间隔（§5.3：v0.2 的刷新机制是前端轮询，SSE 另立项）。
 *  5 秒取自原型标注；页面不可见时跳过一轮，后台标签页不空转打后端。 */
export const ROOM_POLL_MS = 5000;

/** 事件时间线单页条数。取小页是有意的：环境窗是窄栏，小页让「加载后续」的
 *  游标衔接在演示中看得见（CONS-14 的既有取值）。 */
export const ROOM_EVENTS_LIMIT = 6;

const EMPTY_STREAM: RoomStreamPage = { items: [], next_cursor: null };

function client() {
  return createApiClient({
    baseUrl: import.meta.env.VITE_API_BASE ?? "",
    token: import.meta.env.VITE_API_TOKEN ?? "",
  });
}

export async function fetchIssueDetail(issueId: string): Promise<IssueDetailView> {
  if (resolveDataSourceMode() === "replay") {
    if (issueId !== issueDetailFixture.issue_id) throw new Error(`replay 夹具未覆盖 issue ${issueId.slice(0, 8)}`);
    return issueDetailFixture;
  }
  return client().getIssueDetail(issueId);
}

/** §5.1：未建团的 issue 返回空清单且 HTTP 200——空态不是错误，调用方渲染空态。 */
export async function fetchRooms(issueId: string): Promise<RoomListItemView[]> {
  if (resolveDataSourceMode() === "replay") {
    return issueId === issueDetailFixture.issue_id ? roomsFixture : [];
  }
  const res = await client().listRooms(issueId);
  return res.rooms;
}

export async function fetchRoomStream(roomId: string, cursor?: string): Promise<RoomStreamPage> {
  if (resolveDataSourceMode() === "replay") {
    return roomStreamFixtures[roomId] ?? EMPTY_STREAM;
  }
  return client().getRoomStream(roomId, { cursor, limit: ROOM_STREAM_LIMIT });
}

export async function fetchRepositoryPlan(issueId: string, repositoryId: string): Promise<RepositoryPlanView> {
  if (resolveDataSourceMode() === "replay") return repositoryPlanFixture;
  return client().getRepositoryPlan(issueId, repositoryId);
}

/** 该 issue 的当前轮次。环境窗与事件时间线都是**轮次粒度**的消费面，先解析一次
 *  轮次再各自取数——否则两个面各取一遍 issue 详情，同一个事实请求两次。
 *  纯草稿 issue（无轮次）返回 null，调用方按缺口呈现。 */
export async function fetchRoundId(issueId: string): Promise<string | null> {
  if (resolveDataSourceMode() === "replay") {
    return issueDetailFixture.active_round_id ?? issueDetailFixture.latest_round_id;
  }
  const detail = await client().getIssueDetail(issueId);
  return detail.active_round_id ?? detail.latest_round_id;
}

/** 环境窗数据：v0.1 交付聚合是**轮次粒度**，环境窗是**单仓作用域**，所以取该轮次的
 *  聚合再切出本仓那一片。聚合取不到时返回 null，窗内显缺口而非假数字。 */
export async function fetchRepositoryEnv(
  roundId: string,
  repositoryId: string,
): Promise<RepositoryEnv | null> {
  if (resolveDataSourceMode() === "replay") {
    return repositoryEnvFromAggregate(replayAggregate, repositoryId);
  }
  return repositoryEnvFromAggregate(await client().getDelivery(roundId), repositoryId);
}

/** 本轮事件时间线（§4.1）。`kind` 是**服务端**单值过滤（全量语义），
 *  `cursor` 不透明原样回传续读。仓库维度**没有服务端过滤**——§4.1 只定义了 kind，
 *  所以按仓的取舍只能在已加载的这一页里做，呈现时必须说明是当页语义（见 RoomView）。 */
export async function fetchRoundEvents(
  roundId: string,
  opts?: { cursor?: string; kind?: DeliveryEventKind },
): Promise<DeliveryEventsPage> {
  if (resolveDataSourceMode() === "replay") {
    const all = roundEventsFixture.items;
    return {
      items: opts?.kind ? all.filter((e) => e.kind === opts.kind) : all,
      next_cursor: null,
    };
  }
  return client().getEvents(roundId, { ...opts, limit: ROOM_EVENTS_LIMIT });
}
