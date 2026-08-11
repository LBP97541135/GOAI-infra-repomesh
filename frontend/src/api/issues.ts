/** issue 列表数据源：live | replay，开关沿用 `resolveDataSourceMode()`
 *  （URL `?source=live|replay` > `VITE_DATA_SOURCE` > 默认 replay）。
 *
 *  live 打契约 v0.2 §2 的 `GET /issues`；replay 走本地夹具。两侧返回**同一个契约类型**，
 *  页面无分支。 */
import type { IssueListItemView, IssueListResponse } from "./contract";
import { createApiClient } from "./client";
import { resolveGovernanceAgent } from "./decisions";
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

/** 创建 issue（契约 v0.3 §1，验收缺陷 B-1）。仅 live 模式——replay 是回放夹具，
 *  「模拟创建」会篡改夹具世界，调用方在弹窗层挡掉。
 *
 *  处理者 = 花名册派生的 Org Leader（与治理决策同一个单点 resolveGovernanceAgent，
 *  不新增第二条主体取数路径）。幂等键按 §1.3 由客户端生成：每次逻辑创建一个新的
 *  随机 UUID，请求内重试（fetch 层如有）沿用同键；禁止用文本 hash 之类低熵键。 */
export async function createIssue(
  requirementText: string,
  organizationId: string | null,
): Promise<IssueListItemView> {
  const principal = await resolveGovernanceAgent(organizationId);
  if (!principal) {
    throw new Error("处理者未接入：花名册里找不到该工作区的活跃 Org Leader");
  }
  const client = createApiClient({
    baseUrl: import.meta.env.VITE_API_BASE ?? "",
    token: import.meta.env.VITE_API_TOKEN ?? "",
  });
  return client.createIssue({
    requirement_text: requirementText,
    created_by_agent_id: principal.agentId,
    idempotency_key: crypto.randomUUID(),
  });
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
