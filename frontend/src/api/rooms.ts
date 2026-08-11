/** issue 详情 / 房间数据源：live | replay，开关沿用 `resolveDataSourceMode()`。
 *  live 打契约 v0.2 §3 / §5.1 / §5.2 / §5.4；replay 走本地夹具。两侧同一契约类型。 */
import type { IssueDetailView, RepositoryPlanView, RoomListItemView, RoomStreamPage } from "./contract";
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
} from "../data/issueDetail";

/** 房间流单页条数：种子每房间 0-5 条，取 50 足够；真实规模由 next_cursor 续读。 */
export const ROOM_STREAM_LIMIT = 50;

/** 轮询间隔（§5.3：v0.2 的刷新机制是前端轮询，SSE 另立项）。
 *  5 秒取自原型标注；页面不可见时跳过一轮，后台标签页不空转打后端。 */
export const ROOM_POLL_MS = 5000;

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

/** 环境窗数据：v0.1 交付聚合是**轮次粒度**，环境窗是**单仓作用域**，所以取该 issue
 *  当前轮次的聚合再切出本仓那一片。聚合取不到时返回 null，窗内显缺口而非假数字。 */
export async function fetchRepositoryEnv(
  issueId: string,
  repositoryId: string,
): Promise<RepositoryEnv | null> {
  if (resolveDataSourceMode() === "replay") {
    return repositoryEnvFromAggregate(replayAggregate, repositoryId);
  }
  const api = client();
  const detail = await api.getIssueDetail(issueId);
  const roundId = detail.active_round_id ?? detail.latest_round_id;
  if (!roundId) return null;
  const agg = await api.getDelivery(roundId);
  return repositoryEnvFromAggregate(agg, repositoryId);
}
