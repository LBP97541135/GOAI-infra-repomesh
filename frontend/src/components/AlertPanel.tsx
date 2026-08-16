import { useCallback, useEffect, useState } from "react";
import { defaultClient } from "../api/client";
import type {
  AlertEvent,
  AlertMetric,
  AlertOperator,
  AlertRule,
} from "../api/contract";
import { dayLabel, errText, eventTime } from "../display";

/** 观测 · 告警（/api/v1/observe/alerts*）。
 *
 * 数据源：`observability.alert_rules` + `alert_events`。规则按尾随窗口评估
 * llm_usage 聚合指标（成功率 / 错误数 / P95 延迟 / 成本 / 调用数），违反 →
 * firing、恢复 → resolved；后台任务每 60s 评估一轮，「立即评估」按钮手动补一轮。
 * 本组件只渲染后端给的事实：`ActiveAlertBanner`（顶部横幅，30s 轮询）与
 * `AlertPanel`（规则管理 + 告警历史，30s 轮询）。 */

const METRIC_LABEL: Record<string, string> = {
  success_rate: "成功率",
  error_count: "错误次数",
  latency_p95_ms: "P95 延迟 (ms)",
  estimated_cost_usd: "估算成本 (USD)",
  calls: "调用次数",
};
const OP_LABEL: Record<string, string> = { lt: "低于", gt: "高于" };

const eventTimeFull = (iso: string) =>
  `${dayLabel(iso)} ${eventTime(iso)}`;

/** 顶部横幅：正在 firing、未解决的告警。无告警时不渲染任何东西。 */
export function ActiveAlertBanner() {
  const [active, setActive] = useState<AlertEvent[]>([]);

  const load = useCallback(() => {
    defaultClient()
      .activeAlerts()
      .then((res) => setActive(res.events))
      .catch(() => {
        /* 轮询失败静默：横幅消失只是少一条提示，不打断页面 */
      });
  }, []);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 30_000);
    return () => window.clearInterval(t);
  }, [load]);

  if (active.length === 0) return null;

  return (
    <div className="mt-4 rounded-hard border border-salmon/50 bg-salmon/10 px-4 py-3">
      <div className="flex items-center gap-2">
        <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-salmon" />
        <span className="eyebrow text-salmon">在线告警 · {active.length} 条未恢复</span>
        <button
          onClick={() =>
            defaultClient()
              .evaluateAlerts()
              .then((res) => setActive(res.events))
              .catch(() => undefined)
          }
          className="ml-auto rounded border border-line px-2 py-0.5 text-[10.5px] text-tx2 hover:text-tx"
        >
          立即评估
        </button>
      </div>
      <div className="mt-2 space-y-1">
        {active.map((e) => (
          <div key={e.id} className="flex items-baseline justify-between gap-3">
            <span className="text-[12px] text-tx">{e.message}</span>
            <span className="shrink-0 font-mono text-[10.5px] text-tx3">
              {eventTimeFull(e.triggered_at)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** 规则管理 + 告警历史。 */
export function AlertPanel() {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    name: "",
    metric: "success_rate" as AlertMetric,
    operator: "lt" as AlertOperator,
    threshold: "",
    window_minutes: "1440",
  });

  const load = useCallback(() => {
    Promise.all([
      defaultClient().alertRules(),
      defaultClient().alertEvents(7),
    ])
      .then(([r, e]) => {
        setRules(r.rules);
        setEvents(e.events);
      })
      .catch((err: unknown) => setError(errText(err)));
  }, []);

  useEffect(() => {
    load();
    const t = window.setInterval(load, 30_000);
    return () => window.clearInterval(t);
  }, [load]);

  const run = async (op: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await op();
      await load();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const submit = () => {
    const threshold = Number(form.threshold);
    if (!form.name.trim() || Number.isNaN(threshold)) return;
    const windowMinutes = Number(form.window_minutes) || 1440;
    run(() =>
      defaultClient().createAlertRule({
        name: form.name.trim(),
        metric: form.metric,
        operator: form.operator,
        threshold,
        window_minutes: windowMinutes,
      })
    ).then(() =>
      setForm({
        name: "",
        metric: "success_rate",
        operator: "lt",
        threshold: "",
        window_minutes: "1440",
      })
    );
  };

  const toggle = (rule: AlertRule) =>
    run(() => defaultClient().updateAlertRule(rule.id, { enabled: !rule.enabled }));

  const remove = (rule: AlertRule) =>
    run(() => defaultClient().deleteAlertRule(rule.id));

  const thresholdDisplay = (rule: AlertRule) => {
    if (rule.metric === "success_rate") return `${(rule.threshold * 100).toFixed(0)}%`;
    if (rule.metric === "latency_p95_ms") return `${Math.round(rule.threshold)} ms`;
    return String(rule.threshold);
  };

  return (
    <section className="mt-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="eyebrow text-tx2">告警规则</h2>
        <button
          disabled={busy}
          onClick={() =>
            run(() =>
              defaultClient()
                .evaluateAlerts()
                .then(() => undefined)
            )
          }
          className="rounded border border-line px-2 py-0.5 text-[10.5px] text-tx2 hover:text-tx disabled:opacity-50"
        >
          立即评估
        </button>
      </div>

      {error ? <div className="mt-2 text-[11.5px] text-salmon">{error}</div> : null}

      {/* 规则列表 */}
      {rules.length === 0 ? (
        <div className="py-4 text-center text-[11.5px] text-tx3">
          还没有告警规则，在下方添加第一条
        </div>
      ) : (
        <table className="mt-2 w-full">
          <thead>
            <tr className="border-b border-line text-left">
              <th className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">名称</th>
              <th className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">指标</th>
              <th className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">条件</th>
              <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">窗口</th>
              <th className="pb-1.5 text-center text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">状态</th>
              <th className="pb-1.5 text-right text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">操作</th>
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.id} className="border-b border-line/60">
                <td className="py-2 pr-3 text-[11.5px] text-tx">{r.name}</td>
                <td className="py-2 pr-3 text-[11.5px] text-tx2">{METRIC_LABEL[r.metric] ?? r.metric}</td>
                <td className="py-2 pr-3 font-mono text-[11px] text-tx">
                  {OP_LABEL[r.operator] ?? r.operator} {thresholdDisplay(r)}
                </td>
                <td className="py-2 pr-3 text-right font-mono text-[10.5px] text-tx3">
                  {r.window_minutes >= 1440
                    ? `${Math.round(r.window_minutes / 1440)} 天`
                    : `${Math.round(r.window_minutes / 60)} 小时`}
                </td>
                <td className="py-2 text-center">
                  <button
                    onClick={() => toggle(r)}
                    title={r.enabled ? "点击停用" : "点击启用"}
                    className={`rounded-full border px-2 py-0.5 text-[10px] transition-colors ${
                      r.enabled
                        ? "border-olive/60 text-olive hover:border-olive"
                        : "border-line text-tx3 hover:text-tx2"
                    }`}
                  >
                    {r.enabled ? "启用" : "停用"}
                  </button>
                </td>
                <td className="py-2 text-right">
                  <button
                    onClick={() => remove(r)}
                    className="text-[10.5px] text-tx3 hover:text-salmon"
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 新增规则 */}
      <div className="mt-3 flex flex-wrap items-end gap-2 rounded-hard border border-line bg-panel px-3 py-2.5">
        <label className="flex flex-col gap-1 text-[10.5px] text-tx3">
          名称
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="如：成功率过低"
            className="w-32 rounded border border-line bg-elevated px-2 py-1 text-[11.5px] text-cream outline-none focus:border-amber-hi"
          />
        </label>
        <label className="flex flex-col gap-1 text-[10.5px] text-tx3">
          指标
          <select
            value={form.metric}
            onChange={(e) => setForm({ ...form, metric: e.target.value as AlertMetric })}
            className="rounded border border-line bg-elevated px-2 py-1 text-[11.5px] text-cream outline-none focus:border-amber-hi"
          >
            {(Object.keys(METRIC_LABEL) as AlertMetric[]).map((m) => (
              <option key={m} value={m}>{METRIC_LABEL[m]}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[10.5px] text-tx3">
          条件
          <select
            value={form.operator}
            onChange={(e) => setForm({ ...form, operator: e.target.value as AlertOperator })}
            className="rounded border border-line bg-elevated px-2 py-1 text-[11.5px] text-cream outline-none focus:border-amber-hi"
          >
            <option value="lt">低于</option>
            <option value="gt">高于</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[10.5px] text-tx3">
          阈值
          <input
            value={form.threshold}
            onChange={(e) => setForm({ ...form, threshold: e.target.value })}
            placeholder="0.8"
            className="w-24 rounded border border-line bg-elevated px-2 py-1 font-mono text-[11.5px] text-cream outline-none focus:border-amber-hi"
          />
        </label>
        <label className="flex flex-col gap-1 text-[10.5px] text-tx3">
          窗口(分钟)
          <input
            value={form.window_minutes}
            onChange={(e) => setForm({ ...form, window_minutes: e.target.value })}
            placeholder="1440"
            className="w-24 rounded border border-line bg-elevated px-2 py-1 font-mono text-[11.5px] text-cream outline-none focus:border-amber-hi"
          />
        </label>
        <button
          disabled={busy || !form.name.trim() || Number.isNaN(Number(form.threshold))}
          onClick={submit}
          className="rounded border border-amber-hi px-3 py-1 text-[11px] text-amber-hi hover:bg-amber-hi/10 disabled:opacity-40"
        >
          添加规则
        </button>
      </div>

      {/* 告警历史 */}
      <div className="mt-5">
        <h2 className="eyebrow text-tx2">告警历史（近 7 天）</h2>
        {events.length === 0 ? (
          <div className="py-4 text-center text-[11.5px] text-tx3">
            最近 7 天没有触发过告警
          </div>
        ) : (
          <table className="mt-2 w-full">
            <thead>
              <tr className="border-b border-line text-left">
                <th className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">触发时间</th>
                <th className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">规则</th>
                <th className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">消息</th>
                <th className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">状态</th>
                <th className="pb-1.5 text-[10.5px] font-normal tracking-[0.12em] text-tx2 uppercase">恢复时间</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id} className="border-b border-line/60">
                  <td className="py-2 pr-3 font-mono text-[10.5px] text-tx3">
                    {eventTimeFull(e.triggered_at)}
                  </td>
                  <td className="py-2 pr-3 text-[11.5px] text-tx">{e.rule_name ?? "—"}</td>
                  <td className="py-2 pr-3 text-[11px] text-tx2">{e.message}</td>
                  <td className="py-2 pr-3">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-[10px] ${
                        e.status === "firing"
                          ? "border-salmon/60 text-salmon"
                          : "border-olive/60 text-olive"
                      }`}
                    >
                      {e.status === "firing" ? "触发中" : "已恢复"}
                    </span>
                  </td>
                  <td className="py-2 font-mono text-[10.5px] text-tx3">
                    {e.resolved_at ? eventTimeFull(e.resolved_at) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
