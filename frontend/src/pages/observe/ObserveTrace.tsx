import { useCallback, useEffect, useRef, useState } from "react";
import { defaultClient } from "../../api/client";
import type {
  TraceEvent,
  TraceEventsResponse,
  TraceIssueGroupsResponse,
  TraceSession,
  TraceSessionsResponse,
} from "../../api/contract";
import { ErrorPanel, LoadingLine } from "../../components/StatusBlocks";
import { agentLabel, dayLabel, errText, eventTime, shortId } from "../../display";
import { ObserveCrumb } from "./ObserveCrumb";

/** 观测 · 推理轨迹（#/observe/trace）。数据链路：
 *  CoPaw 会话文件（MinIO/本地）→ trace_ingest 轮询解析 → observability.trace_sessions
 *  + trace_events → 本页按「会话时间线 / 跨会话事件 / 按 Issue」三种视图消费。
 *
 *  「按 Issue」是近似归因：会话按 task 键控、与 issue 无字段关联，仅按
 *  issue 活动窗口（usage ∪ logs，两侧各放宽 15 分钟）的时间重叠来疑似关联；
 *  下钻后回到会话时间线并锁定该 issue 的窗口过滤，页面明确标注「疑似」。
 *
 *  排版约定（对抗性审查定稿）：摘要只保留三个关键数字（与门户摘要条同惯例）；
 *  颜色只编码状态（olive=正常 / salmon=失败 / tx3=跳过），类型一律用中性描边徽标——
 *  不新增颜色语义；分页 keyset（sessions/events 倒序、会话事件 seq 升序），30s 轮询。 */

const POLL_MS = 30_000;
const fmt = (n: number) => n.toLocaleString("en-US");

type TraceTab = "sessions" | "stream" | "issues";

/** 类型筛选（跨会话事件流）。null = 全部；值与服务端 event_type 白名单一一对应。 */
const TYPE_FILTERS: ReadonlyArray<{ value: string | null; label: string }> = [
  { value: null, label: "全部" },
  { value: "chat", label: "LLM 推理" },
  { value: "tool", label: "工具" },
  { value: "skill", label: "Skill" },
  { value: "mcp", label: "MCP" },
  { value: "task", label: "任务" },
];

const TYPE_LABEL: Record<string, string> = {
  chat: "LLM 推理",
  tool: "工具",
  skill: "Skill",
  mcp: "MCP",
  rag: "RAG",
  task: "任务",
};

const STATUS_LABEL: Record<string, string> = { ok: "正常", error: "失败", skipped: "跳过" };
const STATUS_DOT: Record<string, string> = { ok: "bg-olive", error: "bg-salmon", skipped: "bg-tx3" };

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

/** 载荷关键字段：按事件类型挑字段展示，避免把整坨 payload 倒出来。 */
const PAYLOAD_KEYS: Record<string, string[]> = {
  task: [
    "task_id",
    "project_id",
    "repository_id",
    "correlation_id",
    "subject",
    "kind",
    "sender_agent_id",
    "recipient_agent_id",
  ],
  tool: ["call_id", "input", "raw_input", "output"],
  skill: ["call_id", "input", "raw_input", "output"],
  mcp: ["call_id", "input", "raw_input", "output"],
  rag: ["call_id", "input", "raw_input", "output"],
};

/** 事件行（会话时间线 / 跨会话事件流共用）。类型=中性徽标、状态=色点，
 *  展开后显示 role / summary / 载荷关键字段。 */
function EventRow({ event, showAgent = false }: { event: TraceEvent; showAgent?: boolean }) {
  const [open, setOpen] = useState(false);
  const typeLabel = TYPE_LABEL[event.event_type] ?? event.event_type;
  return (
    <div className="border-b border-line/60 py-2 last:border-b-0">
      <div className="flex items-center gap-2">
        <span className="w-[64px] shrink-0 rounded border border-line px-1 py-[1px] text-center text-[9.5px] text-tx2">
          {typeLabel}
        </span>
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOT[event.status] ?? "bg-tx3"}`}
          title={statusLabel(event.status)}
        />
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-tx">
          {event.name}
        </span>
        {showAgent && event.agent_name ? (
          <span className="max-w-[180px] shrink-0 truncate rounded border border-line px-1.5 py-[1px] text-[9.5px] text-tx3">
            AGENT {event.agent_name}
          </span>
        ) : null}
        <span className="shrink-0 text-[10.5px] text-tx3">
          {dayLabel(event.ts)} {eventTime(event.ts)}
        </span>
        <button
          onClick={() => setOpen((v) => !v)}
          className="shrink-0 text-[10.5px] text-tx2 transition-colors hover:text-amber-hi"
        >
          {open ? "收起" : "详情"}
        </button>
      </div>
      {open && (
        <div className="mt-1.5 pl-[68px]">
          {event.role ? <p className="text-[10.5px] text-tx3">role: {event.role}</p> : null}
          {event.summary ? (
            <p className="whitespace-pre-wrap break-all font-mono text-[10.5px] leading-relaxed text-tx2">
              {event.summary}
            </p>
          ) : null}
          <PayloadFields event={event} />
        </div>
      )}
    </div>
  );
}

function PayloadFields({ event }: { event: TraceEvent }) {
  const p = event.payload;
  if (!p) return null;
  const rows = (PAYLOAD_KEYS[event.event_type] ?? [])
    .filter((k) => typeof p[k] === "string" || typeof p[k] === "number")
    .map((k) => ({ k, v: String(p[k]) }));
  if (rows.length === 0) return null;
  return (
    <div className="mt-1.5 space-y-0.5">
      {rows.map(({ k, v }) => (
        <div key={k} className="flex gap-2">
          <span className="w-24 shrink-0 font-mono text-[9.5px] text-tx3">{k}</span>
          <span className="min-w-0 break-all font-mono text-[10px] leading-relaxed text-tx2">
            {v.length > 160 ? `${v.slice(0, 160)}…` : v}
          </span>
        </div>
      ))}
    </div>
  );
}

/** 会话卡片：标题行可点击展开时间线；解析异常会话在标题行标注。 */
function SessionCard({
  session,
  expanded,
  onToggle,
  events,
  nextSeq,
  onLoadMore,
}: {
  session: TraceSession;
  expanded: boolean;
  onToggle: () => void;
  events: TraceEvent[] | null;
  nextSeq: number | null;
  onLoadMore: () => void;
}) {
  return (
    <div className="rounded-hard border border-line bg-panel">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left"
      >
        <span className="w-3 shrink-0 text-[10px] text-tx3">{expanded ? "▾" : "▸"}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-tx">
          {agentLabel(session.agent_name, session.id)}
        </span>
        <span className="shrink-0 font-mono text-[10.5px] text-tx3">
          #{shortId(session.session_id)}
        </span>
        <span className="shrink-0 font-mono text-[10.5px] text-tx2">
          {fmt(session.event_count)} 事件
        </span>
        <span className="shrink-0 text-[10.5px] text-tx3">
          {dayLabel(session.first_seen_at)} {eventTime(session.first_seen_at)}
        </span>
        {session.parsing_error ? (
          <span className="shrink-0 rounded-full border border-salmon/60 px-1.5 py-[1px] text-[9.5px] text-salmon">
            解析异常
          </span>
        ) : null}
      </button>
      {expanded && (
        <div className="border-t border-line/60 px-3.5 py-2.5">
          {events === null ? (
            <LoadingLine text="时间线加载中…" className="py-4" />
          ) : events.length === 0 ? (
            <p className="py-2 text-[11px] text-tx3">该会话暂无事件</p>
          ) : (
            <>
              <div>
                {events.map((e) => (
                  <EventRow key={e.id} event={e} />
                ))}
              </div>
              {nextSeq !== null && (
                <button
                  onClick={onLoadMore}
                  className="mt-2 w-full rounded border border-line py-1.5 text-[10.5px] text-tx2 transition-colors hover:border-amber-hi/60 hover:text-amber-hi"
                >
                  加载更多
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-hard border border-line bg-panel px-3.5 py-2.5">
      <p className="text-[10px] text-tx3">{label}</p>
      <p className="mt-1 font-mono text-[22px] leading-none text-cream">{value}</p>
    </div>
  );
}

function EmptyTrace() {
  return (
    <div className="mt-4 rounded-hard border border-line bg-panel px-4 py-10 text-center">
      <p className="text-[12.5px] text-tx2">暂无推理轨迹</p>
      <p className="mt-1.5 text-[11px] leading-relaxed text-tx3">
        配置存储并运行 Agent 后，采集器会轮询解析 .copaw 会话文件；这里将展示每场会话的
        完整推理链路（LLM 推理 / 工具 / Skill / MCP / 任务）与状态。
      </p>
    </div>
  );
}

export function ObserveTrace() {
  const [tab, setTab] = useState<TraceTab>("sessions");
  const [eventType, setEventType] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const [sessions, setSessions] = useState<TraceSessionsResponse | null>(null);
  const [stream, setStream] = useState<TraceEventsResponse | null>(null);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [syncedAt, setSyncedAt] = useState<string | null>(null);

  // 按 Issue 视图（近似归因）与下钻：issueFilter 激活时会话时间线收窄到
  // 该 issue 的活动窗口，并标注「疑似」。
  const [issueFilter, setIssueFilter] = useState<string | null>(null);
  const [groups, setGroups] = useState<TraceIssueGroupsResponse | null>(null);
  const [groupsError, setGroupsError] = useState<string | null>(null);

  const [expanded, setExpanded] = useState<string | null>(null);
  const [timelines, setTimelines] = useState<Record<string, TraceEvent[]>>({});
  const [seqCursors, setSeqCursors] = useState<Record<string, number | null>>({});

  const [tick, setTick] = useState(0);
  const hasSessionsRef = useRef(false);

  const refresh = useCallback(() => {
    setTimelines({});
    setSeqCursors({});
    setTick((n) => n + 1);
  }, []);

  // 30s 心跳
  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), POLL_MS);
    return () => window.clearInterval(t);
  }, []);

  // 会话列表：issueFilter 激活时收窄到该 issue 活动窗口（近似归因）。
  // 首屏失败进错误态；轮询失败静默保旧数据。请求开始即清错误，
  // 避免一次瞬时 502 把整页锁死在错误态（后续轮询成功应自动恢复）。
  useEffect(() => {
    let cancelled = false;
    setPageError(null);
    defaultClient()
      .traceSessions({ limit: 200, issueId: issueFilter ?? undefined })
      .then((r) => {
        if (cancelled) return;
        hasSessionsRef.current = true;
        setSessions(r);
        setSyncedAt(new Date().toISOString());
      })
      .catch((err: unknown) => {
        if (!cancelled && !hasSessionsRef.current) setPageError(errText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [tick, issueFilter]);

  // 按 Issue 分组（仅 issues tab；近似归因，不参与轮询——手动切 tab 时拉取）
  useEffect(() => {
    if (tab !== "issues") return;
    let cancelled = false;
    setGroupsError(null);
    defaultClient()
      .observeTraceIssueGroups()
      .then((r) => {
        if (!cancelled) setGroups(r);
      })
      .catch((err: unknown) => {
        if (!cancelled) setGroupsError(errText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [tab, tick]);

  // 从按 Issue 分组下钻：回到会话时间线并锁定该 issue 的窗口过滤
  const drillIntoIssue = (id: string) => {
    setIssueFilter(id);
    setTab("sessions");
  };

  const clearIssueFilter = () => {
    setIssueFilter(null);
    setTimelines({});
    setSeqCursors({});
  };

  // 跨会话事件流（仅 stream tab；筛选或心跳变化时重取。切换瞬间不清空旧数据，
  // 新数据到达才覆盖——避免闪 LoadingLine）
  useEffect(() => {
    if (tab !== "stream") return;
    let cancelled = false;
    setStreamError(null);
    defaultClient()
      .traceEvents({ eventType: eventType ?? undefined, status: status ?? undefined, limit: 50 })
      .then((r) => {
        if (!cancelled) setStream(r);
      })
      .catch((err: unknown) => {
        if (!cancelled) setStreamError(errText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [tab, eventType, status, tick]);

  // 展开会话的时间线首屏；心跳不重取，避免打断「加载更多」的分页位置
  useEffect(() => {
    if (expanded === null || timelines[expanded] !== undefined) return;
    let cancelled = false;
    defaultClient()
      .traceSessionEvents(expanded, { limit: 100 })
      .then((r) => {
        if (cancelled) return;
        setTimelines((m) => ({ ...m, [expanded]: r.events }));
        setSeqCursors((m) => ({ ...m, [expanded]: r.next_seq }));
      })
      .catch((err: unknown) => {
        if (!cancelled) setPageError(errText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [expanded, timelines]);

  const loadMoreTimeline = useCallback(() => {
    if (expanded === null) return;
    const afterSeq = seqCursors[expanded];
    if (afterSeq === null || afterSeq === undefined) return;
    defaultClient()
      .traceSessionEvents(expanded, { limit: 100, afterSeq })
      .then((r) => {
        setTimelines((m) => ({ ...m, [expanded]: [...(m[expanded] ?? []), ...r.events] }));
        setSeqCursors((m) => ({ ...m, [expanded]: r.next_seq }));
      })
      .catch((err: unknown) => setPageError(errText(err)));
  }, [expanded, seqCursors]);

  const loadMoreStream = useCallback(() => {
    const cursor = stream?.next_cursor;
    if (!cursor) return;
    defaultClient()
      .traceEvents({ eventType: eventType ?? undefined, status: status ?? undefined, limit: 50, cursor })
      .then((r) =>
        setStream((s) =>
          s ? { events: [...s.events, ...r.events], next_cursor: r.next_cursor, next_seq: null } : r,
        ),
      )
      .catch((err: unknown) => setStreamError(errText(err)));
  }, [stream, eventType, status]);

  const toggleSession = useCallback((id: string) => {
    setExpanded((cur) => (cur === id ? null : id));
  }, []);

  const sessionCount = sessions?.sessions.length ?? 0;
  const eventTotal = (sessions?.sessions ?? []).reduce((sum, s) => sum + s.event_count, 0);
  const agentCount = new Set((sessions?.sessions ?? []).map((s) => s.agent_name)).size;

  const controls = (
    <div className="flex items-center gap-3">
      {syncedAt ? (
        <span className="text-[10.5px] text-tx3">同步 {eventTime(syncedAt)}</span>
      ) : null}
      <button
        onClick={refresh}
        className="rounded border border-line px-2 py-1 text-[10.5px] text-tx2 transition-colors hover:border-amber-hi/60 hover:text-amber-hi"
      >
        刷新
      </button>
    </div>
  );

  if (pageError) {
    return (
      <div className="max-w-[860px]">
        <ObserveCrumb section="推理轨迹">{controls}</ObserveCrumb>
        <ErrorPanel title="推理轨迹加载失败" message={pageError} onRetry={refresh} />
      </div>
    );
  }

  if (sessions === null) {
    return (
      <div className="max-w-[860px]">
        <ObserveCrumb section="推理轨迹">{controls}</ObserveCrumb>
        <LoadingLine text="推理轨迹加载中…" />
      </div>
    );
  }

  const tabBtn = (active: boolean) =>
    `rounded px-2.5 py-1 text-[11px] transition-colors ${
      active ? "bg-amber/10 text-amber" : "text-tx2 hover:text-amber-hi"
    }`;

  const pillBtn = (active: boolean) =>
    `rounded-full border px-2 py-[3px] text-[10.5px] transition-colors ${
      active ? "border-amber-hi/70 text-amber" : "border-line text-tx2 hover:text-amber-hi"
    }`;

  return (
    <div className="max-w-[860px]">
      <ObserveCrumb section="推理轨迹">{controls}</ObserveCrumb>

      {/* 摘要：三个关键数字（与门户摘要条同惯例） */}
      <div className="mt-4 grid grid-cols-3 gap-3">
        <MetricCard label="会话" value={fmt(sessionCount)} />
        <MetricCard label="事件" value={fmt(eventTotal)} />
        <MetricCard label="覆盖 Agent" value={fmt(agentCount)} />
      </div>

      {/* 三视图切换 */}
      <div className="mt-4 flex items-center gap-1">
        <button onClick={() => setTab("sessions")} className={tabBtn(tab === "sessions")}>
          会话时间线
        </button>
        <button onClick={() => setTab("stream")} className={tabBtn(tab === "stream")}>
          跨会话事件
        </button>
        <button onClick={() => setTab("issues")} className={tabBtn(tab === "issues")}>
          按 Issue
        </button>
      </div>

      {tab === "issues" ? (
        <div className="mt-3">
          <p className="text-[11px] leading-relaxed text-tx3">
            会话按 <b className="text-tx2">task</b> 键控、与 issue 无字段关联，这里按
            <b className="text-tx2"> 时间重叠</b>近似归因：某 issue 的活动窗口（用量 ∪ 日志，
            两侧各放宽 15 分钟）内开始产生的会话计为疑似。点分组下钻查看窗口内的会话。
          </p>
          {groupsError ? (
            <ErrorPanel
              title="按 Issue 分组加载失败"
              message={groupsError}
              onRetry={refresh}
              className="mt-3"
            />
          ) : groups === null ? (
            <LoadingLine text="按 Issue 分组加载中…" className="mt-3" />
          ) : groups.issues.length === 0 ? (
            <p className="mt-4 rounded-hard border border-line bg-panel px-4 py-8 text-center text-[11.5px] text-tx3">
              暂无疑似关联 · 产生用量/日志的 issue 且窗口内有会话后，这里会按 issue 分组
            </p>
          ) : (
            <div className="mt-3 divide-y divide-line rounded-hard border border-line bg-panel">
              {groups.issues.map((g) => (
                <button
                  key={g.issue_id}
                  onClick={() => drillIntoIssue(g.issue_id)}
                  className="flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-panel/80"
                >
                  <span className="font-mono text-[11.5px] text-cream">#{shortId(g.issue_id)}</span>
                  <span className="truncate font-mono text-[9.5px] text-tx3">{g.issue_id}</span>
                  <span className="ml-auto shrink-0 text-[10.5px] text-tx3">
                    {g.last_session_at ? `最后会话 ${eventTime(g.last_session_at)} · ` : ""}
                    <b className="text-tx2">{g.suspected_sessions}</b> 场疑似
                  </span>
                  <span className="shrink-0 text-[11px] text-tx2 transition-colors hover:text-amber-hi">
                    下钻 →
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      ) : tab === "sessions" ? (
        <div className="mt-3 space-y-2">
          {issueFilter !== null ? (
            <div className="flex items-center gap-2 rounded-hard border border-dashed border-line bg-panel px-3.5 py-2 text-[10.5px] text-tx3">
              <span className="shrink-0">查看 issue</span>
              <span className="truncate font-mono text-tx2">#{shortId(issueFilter)}</span>
              <span className="shrink-0">
                活动窗口内（±15 分钟）的会话——<b className="text-tx2">疑似关联</b>，非精确过滤
              </span>
              <button
                onClick={clearIssueFilter}
                className="ml-auto shrink-0 rounded border border-line px-2 py-0.5 text-tx2 transition-colors hover:border-amber-hi/60 hover:text-amber-hi"
              >
                清除，看全部会话
              </button>
            </div>
          ) : null}
          {sessions.sessions.length === 0 ? (
            issueFilter !== null ? (
              <div className="mt-2 rounded-hard border border-line bg-panel px-4 py-10 text-center">
                <p className="text-[12.5px] text-tx2">该 issue 窗口内暂无疑似会话</p>
                <p className="mt-1.5 text-[11px] leading-relaxed text-tx3">
                  可能是 issue 活动与 agent 会话时间未重叠，或该 issue 尚未触发 agent 执行
                </p>
              </div>
            ) : (
              <EmptyTrace />
            )
          ) : (
            sessions.sessions.map((session) => (
              <SessionCard
                key={session.id}
                session={session}
                expanded={expanded === session.id}
                onToggle={() => toggleSession(session.id)}
                events={timelines[session.id] ?? null}
                nextSeq={seqCursors[session.id] ?? null}
                onLoadMore={loadMoreTimeline}
              />
            ))
          )}
        </div>
      ) : (
        <div className="mt-3">
          {/* 筛选：类型胶囊 + 状态胶囊 */}
          <div className="flex flex-wrap items-center gap-1.5">
            {TYPE_FILTERS.map((f) => (
              <button
                key={f.value ?? "__all__"}
                onClick={() => setEventType(f.value)}
                className={pillBtn(eventType === f.value)}
              >
                {f.label}
              </button>
            ))}
            <span className="mx-1 h-3.5 w-px bg-tx3/40" />
            {[{ value: null, label: "全部状态" }, { value: "error", label: "仅失败" }].map((f) => (
              <button
                key={f.value ?? "__all__"}
                onClick={() => setStatus(f.value)}
                className={pillBtn(status === f.value)}
              >
                {f.label}
              </button>
            ))}
          </div>

          {streamError ? (
            <ErrorPanel
              title="事件流加载失败"
              message={streamError}
              onRetry={refresh}
              className="mt-3"
            />
          ) : stream === null ? (
            <LoadingLine text="事件流加载中…" className="mt-3" />
          ) : stream.events.length === 0 ? (
            <p className="mt-4 rounded-hard border border-line bg-panel px-4 py-8 text-center text-[11.5px] text-tx3">
              该筛选下暂无事件
            </p>
          ) : (
            <>
              <div className="mt-3 rounded-hard border border-line bg-panel px-3.5 py-1">
                {stream.events.map((e) => (
                  <EventRow key={e.id} event={e} showAgent />
                ))}
              </div>
              {stream.next_cursor && (
                <button
                  onClick={loadMoreStream}
                  className="mt-2 w-full rounded border border-line py-1.5 text-[10.5px] text-tx2 transition-colors hover:border-amber-hi/60 hover:text-amber-hi"
                >
                  加载更多
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
