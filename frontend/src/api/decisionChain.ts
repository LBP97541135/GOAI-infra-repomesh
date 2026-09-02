/** 历史决策数据源：live | replay，开关沿用 `resolveDataSourceMode()`
 *  （URL `?source=live|replay` > `VITE_DATA_SOURCE` > 默认 live）。
 *
 *  live 打 decision-chain-v0.1 §6 的四个真实端点（trace / similar /
 *  semantic-search / embeddings-refresh，均要求 Bearer agent_action_token，
 *  client.ts 已带）；replay 走 data/decisionChain.ts 的演示剧本——语义检索用
 *  关键词重合度在夹具语料里算「相似」，两种入口共用同一界面。
 *
 *  **回放模式一律拒绝写**：刷新向量库在真实世界批量调 embedding API 给存量
 *  决策单建索引，夹具里没有可写的 embedding 库。 */
import type {
  DecisionChainView,
  EmbeddingRefreshView,
  SemanticSearchView,
  SimilarDecisionsView,
} from "./contract";
import { defaultClient } from "./client";
import { resolveDataSourceMode, type DataSourceMode } from "./source";
import { errText, shortId } from "../display";
import { fetchIssues } from "./issues";
import {
  DECISION_PROJECT_META,
  replaySemanticSearch,
  replaySimilar,
  replayTrace,
  searchReplayProjects,
} from "../data/decisionChain";

export function decisionChainSourceMode(): DataSourceMode {
  return resolveDataSourceMode();
}

/** §6.1 完整决策链追溯（需求定位入口）。replay 下未知项目抛错——
 *  与 live 的 404 同一语义，不拿空链冒充「该项目没有决策」。 */
export async function fetchDecisionChain(
  projectId: string,
  organizationId?: string | null,
): Promise<DecisionChainView> {
  if (resolveDataSourceMode() === "replay") {
    const chain = replayTrace(projectId);
    if (!chain) {
      throw new Error(
        `replay 夹具未覆盖项目 ${shortId(projectId)}。可选（夹具世界）：${DECISION_PROJECT_META.map((p) => shortId(p.id)).join(" / ")}`,
      );
    }
    return chain;
  }
  return defaultClient().getDecisionChain(projectId, organizationId);
}

/** §6.5 相似历史（同仓 + 最近，structural；语义命中 score 非空）。
 *  live 下 semantic 缺 embedding 端点时后端回退 structural 并由 mode 如实报告。 */
export async function fetchSimilarDecisions(
  projectId: string,
  organizationId?: string | null,
  opts?: { mode?: "structural" | "semantic"; queryText?: string; topK?: number },
): Promise<SimilarDecisionsView> {
  if (resolveDataSourceMode() === "replay") {
    return replaySimilar(projectId, opts?.topK ?? 5);
  }
  return defaultClient().getSimilarDecisions(projectId, organizationId, opts);
}

/** §6.5 扩展：跨组织语义检索（按文本搜历史决策）。 */
export async function searchSemanticDecisions(
  queryText: string,
  opts?: { organizationId?: string | null; topK?: number },
): Promise<SemanticSearchView> {
  if (resolveDataSourceMode() === "replay") {
    return replaySemanticSearch(queryText, opts?.topK ?? 5);
  }
  return defaultClient().semanticSearchDecisions(queryText, opts);
}

/** L3 管理端点：一次批量向量化存量决策单。回放模式拒绝写（同 redispatch）。 */
export async function refreshDecisionEmbeddings(): Promise<EmbeddingRefreshView> {
  if (resolveDataSourceMode() === "replay") {
    throw new Error(
      "回放模式不写后端：刷新向量库在真实世界批量调 embedding API 建索引，夹具里没有可写的 embedding 库。加 ?source=live 后可真实刷新。",
    );
  }
  return defaultClient().refreshDecisionEmbeddings();
}

/** 统一取错文案（历史决策页 catch 分支共用）。 */
export function decisionChainErrText(err: unknown): string {
  return errText(err);
}

/** 需求定位候选（live 与 replay 统一形状；live 侧来自 issue 列表读模型）。 */
export interface DecisionProjectCandidate {
  project_id: string;
  title: string;
  /** 最新决策时间（replay 有；live 从 issue 列表拿不到决策时间 → null） */
  latest_at: string | null;
  /** live 侧 issue 的阶段文案（如「发布门禁」）；replay 无 → null */
  note: string | null;
}

/** 需求定位输入解析（live 用）：剥掉 # 后若像 UUID（带/不带连字符）→ id 直查；
 *  否则当标题关键词搜索。replay 另有 resolveReplayProjectId 兜底短前缀。 */
export function parseProjectInput(
  input: string,
): { kind: "id"; id: string } | { kind: "keyword"; keyword: string } {
  const raw = input.trim().replace(/^#/, "");
  const dashed = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (dashed.test(raw)) return { kind: "id", id: raw.toLowerCase() };
  if (/^[0-9a-f]{32}$/i.test(raw)) {
    const h = raw.toLowerCase();
    return {
      kind: "id",
      id: `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`,
    };
  }
  return { kind: "keyword", keyword: raw };
}

/** 需求定位：输入标题关键词（或 replay 的短前缀），返回候选项目。
 *  - replay：决策链夹具按 id 前缀 / 标题关键词匹配；
 *  - live：issue 列表读模型（open+closed 各自第一页）客户端过滤标题/需求文本，
 *    诚实标注「基于当前加载列表」，不冒充全量。 */
export async function locateProjectCandidates(
  keyword: string,
  organizationId?: string | null,
): Promise<DecisionProjectCandidate[]> {
  const kw = keyword.trim();
  if (!kw) return [];
  if (resolveDataSourceMode() === "replay") {
    return searchReplayProjects(kw).map((c) => ({
      project_id: c.project_id,
      title: c.title,
      latest_at: c.latest_at,
      note: null,
    }));
  }
  const [open, closed] = await Promise.all([
    fetchIssues({ state: "open", organizationId: organizationId ?? undefined }),
    fetchIssues({ state: "closed", organizationId: organizationId ?? undefined }),
  ]);
  const needle = kw.toLowerCase();
  return [...open.issues, ...closed.issues]
    .filter(
      (i) =>
        i.title.toLowerCase().includes(needle) ||
        (i.requirement_text ?? "").toLowerCase().includes(needle),
    )
    .map((i) => ({
      project_id: i.issue_id, // issue_id 即决策链 project_id（E1 根同源）
      title: i.title,
      latest_at: null,
      note: i.phase_note,
    }));
}
