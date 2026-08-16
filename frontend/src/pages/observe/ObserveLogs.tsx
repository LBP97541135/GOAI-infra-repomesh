import { useCallback, useEffect, useState } from "react";
import { defaultClient } from "../../api/client";
import type {
  IssueLogGroupsResponse,
  LogEntriesResponse,
} from "../../api/contract";
import { ErrorPanel, LoadingLine } from "../../components/StatusBlocks";
import { errText, eventTime, shortId } from "../../display";
import { ObserveCrumb } from "./ObserveCrumb";

/** 观测 · 日志（#/observe/logs）。数据链路：
 *  RepoMesh 进程根 logger → logging handler → 有界队列 → 后台批量落库 →
 *  observability.log_entries → 本页按「级别 / 来源 / Issue / 全文」检索，
 *  keyset 分页（(ts, id) 倒序）。
 *
 *  查询型页面：不做 30s 自动轮询——自动刷新会不断打断用户正在读的分页位置；
 *  门户卡片的「N+ 条日志」计数已反映最新状态，这里提供手动刷新。
 *  级别用颜色编码严重度（tx3→tx2→amber→salmon），其余一律中性描边；颜色
 *  只表达级别，不引入新的颜色语义（与对抗性审查定稿一致）。 */

const LEVEL_OPTIONS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;

const LEVEL_PILL: Record<string, string> = {
  DEBUG: "border-line text-tx3",
  INFO: "border-line text-tx2",
  WARNING: "border-amber/50 text-amber",
  ERROR: "border-salmon/60 text-salmon",
  CRITICAL: "border-salmon text-salmon",
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 已应用的筛选参数（草稿变化不立刻打 API，点「应用」/回车才生效）。 */
interface LogFilters {
  level?: string;
  source?: string;
  issueId?: string;
  query?: string;
}

const EMPTY_FILTERS: LogFilters = {};

export function ObserveLogs() {
  const [level, setLevel] = useState("");
  const [source, setSource] = useState("");
  const [issueId, setIssueId] = useState("");
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState<LogFilters>(EMPTY_FILTERS);
  const [filterError, setFilterError] = useState<string | null>(null);
  const [data, setData] = useState<LogEntriesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [syncedAt, setSyncedAt] = useState<string | null>(null);

  // 双视图：时间流（查询型）/ 按 Issue（分组型，最近活跃优先）。
  const [view, setView] = useState<"timeline" | "byIssue">("timeline");
  const [groups, setGroups] = useState<IssueLogGroupsResponse | null>(null);
  const [groupsError, setGroupsError] = useState<string | null>(null);

  const fetchGroups = useCallback(async () => {
    setGroupsError(null);
    setGroups(null);
    try {
      const res = await defaultClient().observeLogIssueGroups();
      setGroups(res);
    } catch (err: unknown) {
      setGroupsError(errText(err));
    }
  }, []);

  // 分组下钻：不离开日志板块，切回时间流视图并锁定该 issue 的筛选。
  const drillIntoIssue = (id: string) => {
    setView("timeline");
    setIssueId(id);
    const filters = { ...applied, issueId: id };
    setApplied(filters);
    setFilterError(null);
    fetchFirst(filters);
  };

  const fetchFirst = useCallback(async (filters: LogFilters) => {
    setError(null);
    setData(null);
    try {
      const res = await defaultClient().observeLogs({ ...filters, limit: 50 });
      setData(res);
      setSyncedAt(new Date().toISOString());
    } catch (err: unknown) {
      setError(errText(err));
    }
  }, []);

  // 初始加载：空筛选第一页
  useEffect(() => {
    fetchFirst(EMPTY_FILTERS);
  }, [fetchFirst]);

  const applyFilters = () => {
    if (issueId.trim() && !UUID_RE.test(issueId.trim())) {
      setFilterError("Issue ID 需为 UUID 格式（8-4-4-4-12）");
      return;
    }
    setFilterError(null);
    const filters: LogFilters = {};
    if (level) filters.level = level;
    if (source.trim()) filters.source = source.trim();
    if (issueId.trim()) filters.issueId = issueId.trim();
    if (query.trim()) filters.query = query.trim();
    setApplied(filters);
    fetchFirst(filters);
  };

  const refresh = () => {
    setFilterError(null);
    fetchFirst(applied);
  };

  const loadMore = async () => {
    if (!data?.next_cursor) return;
    setLoadingMore(true);
    try {
      const next = await defaultClient().observeLogs({
        ...applied,
        limit: 50,
        cursor: data.next_cursor,
      });
      setData((cur) =>
        cur ? { ...next, logs: [...cur.logs, ...next.logs] } : next,
      );
    } catch (err: unknown) {
      setError(errText(err));
    } finally {
      setLoadingMore(false);
    }
  };

  const inputCls =
    "rounded border border-line bg-panel px-2 py-1 text-[11px] text-cream placeholder:text-tx3/60 outline-none transition-colors focus:border-amber-hi/60";

  const controls = (
    <div className="flex items-center gap-3">
      {syncedAt ? (
        <span className="text-[10.5px] text-tx3">同步 {eventTime(syncedAt)}</span>
      ) : null}
      <button
        onClick={view === "byIssue" ? fetchGroups : refresh}
        className="rounded border border-line px-2 py-1 text-[10.5px] text-tx2 transition-colors hover:border-amber-hi/60 hover:text-amber-hi"
      >
        刷新
      </button>
    </div>
  );

  const hasFilters = Object.keys(applied).length > 0;
  const shown = data?.logs.length ?? 0;

  const switchView = (next: "timeline" | "byIssue") => {
    if (next === view) return;
    setView(next);
    if (next === "byIssue" && groups === null && groupsError === null) {
      fetchGroups();
    }
  };

  return (
    <div className="max-w-[860px]">
      <ObserveCrumb section="日志">{controls}</ObserveCrumb>

      {/* 双视图切换：时间流（查询型）/ 按 Issue（分组型） */}
      <div className="mt-4 flex gap-1 border-b border-line">
        {(
          [
            ["timeline", "时间流"],
            ["byIssue", "按 Issue"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => switchView(key)}
            className={`border-b-2 px-3 pb-1.5 text-[11.5px] transition-colors ${
              view === key
                ? "border-amber text-cream"
                : "border-transparent text-tx3 hover:text-tx2"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {view === "byIssue" ? (
        <>
          <p className="mt-3 text-[11px] leading-relaxed text-tx3">
            日志按发现链执行上下文（ambient-context）归因到 issue，最近活跃优先。
            点击分组进入该 issue 的详情。
          </p>
          {groupsError ? (
            <ErrorPanel
              title="日志分组加载失败"
              message={groupsError}
              onRetry={fetchGroups}
              className="mt-3"
            />
          ) : groups === null ? (
            <LoadingLine text="日志分组加载中…" className="mt-3" />
          ) : groups.issues.length === 0 ? (
            <p className="mt-4 rounded-hard border border-line bg-panel px-4 py-8 text-center text-[11.5px] text-tx3">
              暂无归因日志 · 日志在发现链上下文内产生后会按 issue 分组出现在这里
            </p>
          ) : (
            <div className="mt-3 divide-y divide-line rounded-hard border border-line bg-panel">
              {groups.issues.map((g) => (
                <button
                  key={g.issue_id}
                  onClick={() => drillIntoIssue(g.issue_id)}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-panel/80"
                >
                  <span className="font-mono text-[11.5px] text-cream">
                    #{shortId(g.issue_id)}
                  </span>
                  <span className="truncate font-mono text-[9.5px] text-tx3">
                    {g.issue_id}
                  </span>
                  <span className="ml-auto shrink-0 text-[10.5px] text-tx3">
                    {g.last_at ? `最后 ${eventTime(g.last_at)} · ` : ""}
                    <b className="text-tx2">{g.count}</b> 条
                  </span>
                  <span className="shrink-0 text-[11px] text-tx2 transition-colors hover:text-amber-hi">
                    下钻 →
                  </span>
                </button>
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          {/* 筛选区：级别下拉 + 来源/Issue 输入 + 全文检索 */}
      <div className="mt-4 rounded-hard border border-line bg-panel px-3.5 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className={`${inputCls} bg-panel`}
            aria-label="级别"
          >
            <option value="">全部级别</option>
            {LEVEL_OPTIONS.map((lv) => (
              <option key={lv} value={lv}>
                {lv}
              </option>
            ))}
          </select>
          <input
            value={source}
            onChange={(e) => setSource(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyFilters()}
            placeholder="来源（logger 名子串）"
            className={`${inputCls} w-44`}
            aria-label="来源"
          />
          <input
            value={issueId}
            onChange={(e) => setIssueId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyFilters()}
            placeholder="Issue ID（UUID）"
            className={`${inputCls} w-40`}
            aria-label="Issue ID"
          />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyFilters()}
            placeholder="全文检索（消息 / 堆栈）"
            className={`${inputCls} w-48`}
            aria-label="全文检索"
          />
          <button
            onClick={applyFilters}
            className="rounded border border-line px-2.5 py-1 text-[11px] text-tx2 transition-colors hover:border-amber-hi/60 hover:text-amber-hi"
          >
            应用筛选
          </button>
        </div>
        {filterError ? (
          <p className="mt-2 text-[10.5px] text-salmon">{filterError}</p>
        ) : null}
      </div>

      {error ? (
        <ErrorPanel
          title="日志加载失败"
          message={error}
          onRetry={refresh}
          className="mt-3"
        />
      ) : data === null ? (
        <LoadingLine text="日志加载中…" className="mt-3" />
      ) : data.logs.length === 0 ? (
        <p className="mt-4 rounded-hard border border-line bg-panel px-4 py-8 text-center text-[11.5px] text-tx3">
          {hasFilters ? "该筛选下暂无日志" : "暂无日志 · 进程日志经采集管道落库后在此展示"}
        </p>
      ) : (
        <>
          <div className="mt-4 flex items-baseline justify-between">
            <span className="text-[10.5px] text-tx3">
              当前 {shown} 条
              {data.next_cursor ? " · 还有更多页" : ""}
            </span>
          </div>
          <div className="mt-1.5 divide-y divide-line rounded-hard border border-line bg-panel px-3.5">
            {data.logs.map((row) => (
              <div key={row.id} className="py-2.5">
                <div className="flex min-w-0 items-center gap-2">
                  <span
                    className={`shrink-0 rounded-full border px-1.5 py-px text-[9.5px] font-medium ${LEVEL_PILL[row.level] ?? "border-line text-tx2"}`}
                  >
                    {row.level}
                  </span>
                  <span className="shrink-0 font-mono text-[10.5px] text-tx3">
                    {eventTime(row.ts)}
                  </span>
                  <span className="truncate font-mono text-[10.5px] text-tx2">
                    {row.source}
                  </span>
                  {row.issue_id ? (
                    <button
                      onClick={() => drillIntoIssue(row.issue_id as string)}
                      title="只看该 issue 的日志"
                      className="ml-auto shrink-0 rounded border border-line px-1.5 py-px text-[9.5px] text-tx3 transition-colors hover:border-amber-hi/60 hover:text-amber-hi"
                    >
                      #{shortId(row.issue_id)}
                    </button>
                  ) : null}
                </div>
                <p className="mt-1.5 whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-cream">
                  {row.message}
                </p>
                {row.exc_info ? (
                  <details className="mt-1.5">
                    <summary className="cursor-pointer text-[10px] text-tx3 transition-colors hover:text-amber-hi">
                      展开堆栈
                    </summary>
                    <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap break-words rounded bg-panel px-2.5 py-2 font-mono text-[10px] leading-relaxed text-salmon">
                      {row.exc_info}
                    </pre>
                  </details>
                ) : null}
              </div>
            ))}
          </div>
          {data.next_cursor ? (
            <button
              onClick={loadMore}
              disabled={loadingMore}
              className="mt-2 w-full rounded border border-line py-1.5 text-[10.5px] text-tx2 transition-colors hover:border-amber-hi/60 hover:text-amber-hi disabled:opacity-50"
            >
              {loadingMore ? "加载中…" : "加载更多"}
            </button>
          ) : null}
        </>
      )}
        </>
      )}
    </div>
  );
}
