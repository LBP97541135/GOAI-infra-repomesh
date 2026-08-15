import { useCallback, useEffect, useRef, useState } from "react";
import { defaultClient } from "../../api/client";
import type { ObserveIssuesResponse, ObserveSummary } from "../../api/contract";
import { dayLabel, errText, eventTime, shortId } from "../../display";
import { ErrorPanel, LoadingLine } from "../../components/StatusBlocks";
import { ObserveCrumb } from "./ObserveCrumb";

/** 观测 · 用量大盘（#/observe/usage）。
 *
 * 数据源：`observability.llm_usage` —— deepseek.py 在每次 chat() 结束时把
 * usage 经线程安全队列落库；发现链各步经 contextvar 带上 issue_id / step 归属。
 * 两个端点：`GET /observe/summary?days=`（聚合大盘，7/30/90 可切）+ `GET
 * /observe/issues`（按 issue 分组、最近活跃优先）。**只渲染后端给的事实**：
 * 空库时标量归零、数组为空，页面如实显示空态，不编造「0 次推理」之外的叙述。
 *
 * 页面行为：30s 自动轮询（失败静默、保留旧数据，不闪断）+ 手动刷新按钮。
 * 窗口切换只在收到新数据后生效，避免切换瞬间闪空。
 *
 * step 编号沿用 v0.4 §3.2 的 GUI_STEP_OF：1 需求分析 / 2 候选评分 /
 * 3 三档分类（审批同列）/ 4 生成计划；null = 发现链外的调用。 */

const STEP_LABEL: Record<number, string> = {
  1: "需求分析",
  2: "候选评分",
  3: "三档分类",
  4: "生成计划",
};

const DAY_OPTIONS = [7, 30, 90] as const;
const POLL_MS = 30_000;

const fmt = (n: number) => n.toLocaleString("en-US");
/** 小额（<1 分）保留 6 位小数，否则 2 位——避免 0.00003645 显示成 $0.00。 */
const usd = (n: number) => (n >= 0.01 ? `$${n.toFixed(2)}` : `$${n.toFixed(6)}`);

function MetricCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-hard border border-line bg-panel px-4 py-3">
      <div className="eyebrow text-tx2">{label}</div>
      <div className="mt-1 font-mono text-[18px] leading-tight text-cream" title={hint}>
        {value}
      </div>
      {hint ? <div className="mt-0.5 text-[10px] text-tx3">{hint}</div> : null}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="eyebrow text-tx2">{children}</h2>;
}

/** 空态：后端零记录时整块替换正文，连表头都不留——表头对着空数组是噪音。 */
function EmptyObserve() {
  return (
    <div className="py-10 text-center">
      <div className="text-[13px] text-tx2">还没有 LLM 调用记录</div>
      <p className="mx-auto mt-2 max-w-[520px] text-[11.5px] leading-relaxed text-tx3">
        数据来自规划侧发现链的 LLM 推理（deepseek.py 每次 chat() 结束时把 usage
        写入 observability.llm_usage）。发起一次 issue 的发现链（需求分析 → 候选
        评分 → 三档分类 → 生成计划）后，这里就会出现系统大盘与按 issue 的消耗汇总。
      </p>
    </div>
  );
}

export function ObserveUsage() {
  const [days, setDays] = useState<number>(7);
  const [summary, setSummary] = useState<ObserveSummary | null>(null);
  const [issues, setIssues] = useState<ObserveIssuesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const hasDataRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    Promise.all([defaultClient().observeSummary(days), defaultClient().observeIssues()])
      .then(([s, i]) => {
        if (cancelled) return;
        hasDataRef.current = true;
        setSummary(s);
        setIssues(i);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 轮询失败静默：保留旧数据继续展示，只有从未成功过才报错。
        if (!hasDataRef.current) setError(errText(err));
      });
    return () => {
      cancelled = true;
    };
  }, [days, tick]);

  // 30s 轮询；切窗口时 interval 无需重置（tick 只是触发 effect，窗口值取自 state）。
  useEffect(() => {
    const t = window.setInterval(() => setTick((n) => n + 1), POLL_MS);
    return () => window.clearInterval(t);
  }, []);

  const refresh = useCallback(() => setTick((n) => n + 1), []);

  const controls = (
    <div className="flex items-center gap-1.5">
      {DAY_OPTIONS.map((d) => (
        <button
          key={d}
          onClick={() => setDays(d)}
          className={`rounded border px-2 py-0.5 text-[11px] transition-colors ${
            d === days
              ? "border-amber-hi text-amber-hi"
              : "border-line text-tx2 hover:text-tx"
          }`}
        >
          {d} 天
        </button>
      ))}
      <button
        onClick={refresh}
        className="rounded border border-line px-2 py-0.5 text-[11px] text-tx2 hover:text-tx"
      >
        刷新
      </button>
    </div>
  );

  if (error) {
    return (
      <div className="max-w-[860px]">
        <ObserveCrumb section="用量大盘">{controls}</ObserveCrumb>
        <ErrorPanel title="用量数据加载失败" message={error} onRetry={refresh} />
      </div>
    );
  }
  if (summary === null || issues === null) {
    return (
      <div className="max-w-[860px]">
        <ObserveCrumb section="用量大盘">{controls}</ObserveCrumb>
        <LoadingLine text="用量数据加载中…" />
      </div>
    );
  }
  if (summary.calls === 0) {
    return (
      <div className="max-w-[860px]">
        <ObserveCrumb section="用量大盘">{controls}</ObserveCrumb>
        <EmptyObserve />
      </div>
    );
  }

  const span =
    summary.first_usage_at && summary.last_usage_at
      ? `${dayLabel(summary.first_usage_at)} ${eventTime(summary.first_usage_at)} → ${dayLabel(summary.last_usage_at)} ${eventTime(summary.last_usage_at)}`
      : "—";
  const maxDaily = Math.max(1, ...summary.daily.map((d) => d.calls));
  const stepRows = [...summary.by_step].sort((a, b) => (a.step ?? 99) - (b.step ?? 99));

  return (
    <div className="max-w-[860px]">
      <ObserveCrumb section="用量大盘">{controls}</ObserveCrumb>

      {/* 系统级大盘：6 卡 = 赛事要求的 Metrics 类（调用 / 成本 / 成功率 / 延迟） */}
      <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3">
        <MetricCard
          label="调用次数"
          value={fmt(summary.calls)}
          hint={`${fmt(summary.success_calls)} 成功 · ${fmt(summary.error_calls)} 失败`}
        />
        <MetricCard
          label="总 tokens"
          value={fmt(summary.total_tokens)}
          hint={`${fmt(summary.prompt_tokens)} prompt + ${fmt(summary.completion_tokens)} completion`}
        />
        <MetricCard
          label="估算成本"
          value={usd(summary.estimated_cost_usd)}
          hint="按服务端单价表估算（USD，可校准）"
        />
        <MetricCard
          label="成功率"
          value={summary.success_rate === null ? "—" : `${(summary.success_rate * 100).toFixed(1)}%`}
          hint={`${fmt(summary.success_calls)} 成功 / ${fmt(summary.calls)} 总调用`}
        />
        <MetricCard
          label="平均延迟"
          value={summary.avg_latency_ms === null ? "—" : `${Math.round(summary.avg_latency_ms)} ms`}
          hint={`p50 ${summary.latency_p50_ms === null ? "—" : `${Math.round(summary.latency_p50_ms)} ms`} · p95 ${summary.latency_p95_ms === null ? "—" : `${Math.round(summary.latency_p95_ms)} ms`}`}
        />
        <MetricCard label="时间跨度" value={span} hint="窗口内首次 → 最近一次使用（UTC）" />
      </div>

      {/* 每日趋势 + 模型分布（含成本） */}
      <div className="mt-6 grid grid-cols-1 gap-6 md:grid-cols-2">
        <section>
          <SectionTitle>每日调用</SectionTitle>
          <div className="mt-2 space-y-2">
            {summary.daily.length === 0 ? (
              <div className="text-[11.5px] text-tx3">窗口内无按天分桶</div>
            ) : (
              summary.daily.map((d) => (
                <div key={d.date} className="flex items-center gap-2">
                  <span className="w-10 shrink-0 font-mono text-[10.5px] text-tx2">
                    {dayLabel(d.date)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="h-1.5 rounded-full bg-olive/80" style={{ width: `${Math.max(2, Math.round((d.calls / maxDaily) * 100))}%` }} />
                  </div>
                  <span className="w-12 shrink-0 text-right font-mono text-[10.5px] text-tx">
                    {fmt(d.calls)}
                  </span>
                </div>
              ))
            )}
          </div>
        </section>

        <section>
          <SectionTitle>按模型</SectionTitle>
          <div className="mt-2 space-y-1.5">
            {summary.by_model.length === 0 ? (
              <div className="text-[11.5px] text-tx3">无模型分桶</div>
            ) : (
              summary.by_model.map((m) => (
                <div
                  key={m.model}
                  className="flex items-baseline justify-between gap-2 border-b border-line/60 pb-1.5"
                >
                  <span className="font-mono text-[11.5px] text-tx">{m.model}</span>
                  <span className="shrink-0 text-[10.5px] text-tx2">
                    {fmt(m.calls)} 次 · {fmt(m.prompt_tokens + m.completion_tokens)} tok · {usd(m.estimated_cost_usd)}
                  </span>
                </div>
              ))
            )}
          </div>
        </section>
      </div>

      {/* 最近异常：失败证据摘要，完整检索在日志板块 */}
      {summary.recent_errors.length > 0 && (
        <section className="mt-6">
          <SectionTitle>最近异常（最多 5 条）</SectionTitle>
          <div className="mt-2 space-y-1.5">
            {summary.recent_errors.map((e, idx) => (
              <div
                key={`${e.created_at}-${idx}`}
                className="flex flex-wrap items-baseline justify-between gap-x-3 border-b border-line/60 pb-1.5"
              >
                <span className="font-mono text-[11px] text-salmon">
                  {e.model} · {e.operation}
                </span>
                <span className="text-[10.5px] text-tx3">
                  {dayLabel(e.created_at)} {eventTime(e.created_at)}
                  {e.finish_reason ? ` · ${e.finish_reason}` : ""}
                  {e.latency_ms !== null ? ` · ${Math.round(e.latency_ms)} ms` : ""}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-1.5 text-[10.5px] text-tx3">
            完整检索见<b className="text-tx2"> 日志</b>板块（建设中）。
          </p>
        </section>
      )}

      {/* 发现链步级 */}
      <section className="mt-6">
        <SectionTitle>发现链步级</SectionTitle>
        <table className="mt-2 w-full">
          <thead>
            <tr className="border-b border-line text-left">
              <th className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">步</th>
              <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">调用</th>
              <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">Prompt</th>
              <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">Completion</th>
            </tr>
          </thead>
          <tbody>
            {stepRows.map((s) => (
              <tr key={s.step ?? "null"} className="border-b border-line/60">
                <td className="py-2 text-[11.5px] text-tx">
                  {s.step === null ? "发现链外" : `${STEP_LABEL[s.step] ?? `Step ${s.step}`}`}
                </td>
                <td className="py-2 text-right font-mono text-[11.5px] text-tx">{fmt(s.calls)}</td>
                <td className="py-2 text-right font-mono text-[11.5px] text-tx2">{fmt(s.prompt_tokens)}</td>
                <td className="py-2 text-right font-mono text-[11.5px] text-tx2">{fmt(s.completion_tokens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* Issue 级汇总（含单 issue 成本归因） */}
      <section className="mt-6">
        <SectionTitle>按 Issue 汇总（最近活跃优先）</SectionTitle>
        {issues.issues.length === 0 ? (
          <div className="py-4 text-center text-[11.5px] text-tx3">
            窗口内没有带 issue 归属的调用
          </div>
        ) : (
          <table className="mt-2 w-full">
            <thead>
              <tr className="border-b border-line text-left">
                <th className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">Issue</th>
                <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">调用</th>
                <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">Prompt</th>
                <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">Completion</th>
                <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">成本</th>
                <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">平均延迟</th>
                <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">最近使用</th>
              </tr>
            </thead>
            <tbody>
              {issues.issues.map((row) => (
                <tr key={row.issue_id} className="border-b border-line/60">
                  <td className="py-2 pr-3 font-mono text-[11.5px] text-amber-hi">#{shortId(row.issue_id)}</td>
                  <td className="py-2 pr-3 text-right font-mono text-[11.5px] text-tx">{fmt(row.calls)}</td>
                  <td className="py-2 pr-3 text-right font-mono text-[11.5px] text-tx2">{fmt(row.prompt_tokens)}</td>
                  <td className="py-2 pr-3 text-right font-mono text-[11.5px] text-tx2">{fmt(row.completion_tokens)}</td>
                  <td className="py-2 pr-3 text-right font-mono text-[11.5px] text-tx2">{usd(row.estimated_cost_usd)}</td>
                  <td className="py-2 pr-3 text-right font-mono text-[11.5px] text-tx2">
                    {row.avg_latency_ms === null ? "—" : `${Math.round(row.avg_latency_ms)} ms`}
                  </td>
                  <td className="py-2 text-right font-mono text-[10.5px] text-tx3">
                    {row.last_usage_at ? `${dayLabel(row.last_usage_at)} ${eventTime(row.last_usage_at)}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <p className="pt-4 text-[11px] leading-relaxed text-tx3">
        采集：deepseek.py 每次 chat() 结束以 OTel GenAI 语义（gen_ai.*）记录 usage，
        经线程安全队列异步落库，失败不阻断推理（队列满则丢弃并计数）。成本为按模型
        单价表的估算值（服务端常量，可校准）。聚合：窗口、按模型、发现链步级与 issue
        分组由服务端
        <b className="text-tx2"> /api/v1/observe/summary</b> 与
        <b className="text-tx2"> /api/v1/observe/issues</b> 计算，前端只渲染。
      </p>
    </div>
  );
}
