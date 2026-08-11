/** issue 列表数据源：live | replay，开关沿用 `resolveDataSourceMode()`
 *  （URL `?source=live|replay` > `VITE_DATA_SOURCE` > 默认 replay）。
 *
 *  live 打契约 v0.2 §2 的 `GET /issues`；replay 走本地夹具。两侧返回**同一个契约类型**，
 *  页面无分支。 */
import type { IssueListResponse } from "./contract";
import { createApiClient } from "./client";
import { resolveDataSourceMode, type DataSourceMode } from "./source";
import { issuesFixture } from "../data/issues";

/** 单页条数。联调种子仅四条，取 20 足够；真实规模下由 next_cursor 续读。 */
export const ISSUES_PAGE_LIMIT = 20;

export interface IssuesQuery {
  state: "open" | "closed";
  /** Q2：工作区由前端持有并传参，服务端不猜。未选工作区时不传 = 全部。 */
  organizationId?: string;
  cursor?: string;
}

function replayPage(q: IssuesQuery): IssueListResponse {
  const all = issuesFixture.issues;
  return {
    issues: all.filter((i) => i.state === q.state),
    open_count: all.filter((i) => i.state === "open").length,
    closed_count: all.filter((i) => i.state === "closed").length,
    // 夹具即全量，没有第二页——不给一个点了没反应的「加载更多」
    next_cursor: null,
  };
}

export function issuesSourceMode(): DataSourceMode {
  return resolveDataSourceMode();
}

export async function fetchIssues(q: IssuesQuery): Promise<IssueListResponse> {
  if (issuesSourceMode() === "replay") return replayPage(q);

  const client = createApiClient({
    baseUrl: import.meta.env.VITE_API_BASE ?? "",
    token: import.meta.env.VITE_API_TOKEN ?? "",
  });
  return client.listIssues({
    state: q.state,
    organizationId: q.organizationId,
    cursor: q.cursor,
    limit: ISSUES_PAGE_LIMIT,
  });
}
