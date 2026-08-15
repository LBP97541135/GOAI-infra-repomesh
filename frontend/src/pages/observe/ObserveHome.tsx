import { useEffect, useState } from "react";
import { defaultClient } from "../../api/client";
import type { ObserveSummary } from "../../api/contract";
import type { ObserveSection } from "../../routes";
import { ActiveAlertBanner } from "../../components/AlertPanel";

const fmt = (n: number) => n.toLocaleString("en-US");

/** 观测中心门户（#/observe）。
 *
 * 不是数据页，是「去哪看」的索引：顶部告警横幅 + 一行健康摘要 + 板块卡片
 * 网格。每个板块对应赛题可观测要求的一个覆盖面（Metrics / Log / 告警 /
 * 推理轨迹），点击卡片跳转 `#/observe/{section}`。已实心的板块卡片带真实
 * 数字；建设中板块带「建设中」徽标，进入后由占位页如实说明边界——不编造
 * 「已接入」。摘要条只在进入时拉一次（门户不需要 30s 轮询的实时性）。 */

const SECTION_CARDS: Array<{
  section: ObserveSection;
  title: string;
  desc: string;
  status: "ready" | "building";
  icon: string;
}> = [
  {
    section: "trace",
    title: "推理轨迹",
    desc: "Trace · Skill 调用 / MCP 工具 / RAG 检索 / Agent 会话全链路（赛题点名覆盖项）",
    status: "ready",
    icon: "⌁",
  },
  {
    section: "usage",
    title: "用量大盘",
    desc: "Metrics · LLM token / 成本 / 延迟 / 成功率聚合、趋势、模型分布、Issue 归因",
    status: "ready",
    icon: "◈",
  },
  {
    section: "logs",
    title: "日志",
    desc: "Log · 统一日志查询（级别 / 来源 / Issue / 全文检索），支撑异常定位",
    status: "ready",
    icon: "✎",
  },
  {
    section: "alerts",
    title: "告警",
    desc: "在线监控与告警 · 阈值规则 + 触发历史，命中即时可见",
    status: "ready",
    icon: "⚠",
  },
];

export function ObserveHome() {
  const [summary, setSummary] = useState<ObserveSummary | null>(null);
  const [activeCount, setActiveCount] = useState<number | null>(null);
  // 推理轨迹卡片统计：keyset 首屏 limit=200 已覆盖现实体量；next_cursor 非空时
  // 用「N+」如实标注还有更多页，绝不拿首页条数冒充总数。
  const [traceCount, setTraceCount] = useState<number | null>(null);
  const [traceHasMore, setTraceHasMore] = useState(false);
  // 日志卡片统计：同样用 keyset 首屏条数 + 「+」标注还有更多页。
  const [logCount, setLogCount] = useState<number | null>(null);
  const [logHasMore, setLogHasMore] = useState(false);

  useEffect(() => {
    let cancelled = false;
    // 门户只做一次摘要快照；各路独立降级——某一路端点失败不拖垮其余数字
    // （只渲染后端给的事实，拿不到的那格如实显示 —）。
    Promise.allSettled([
      defaultClient().observeSummary(7),
      defaultClient().activeAlerts(),
      defaultClient().traceSessions({ limit: 200 }),
      defaultClient().observeLogs({ limit: 200 }),
    ]).then(([s, a, t, l]) => {
      if (cancelled) return;
      if (s.status === "fulfilled") setSummary(s.value);
      if (a.status === "fulfilled") setActiveCount(a.value.events.length);
      if (t.status === "fulfilled") {
        setTraceCount(t.value.sessions.length);
        setTraceHasMore(t.value.next_cursor !== null);
      }
      if (l.status === "fulfilled") {
        setLogCount(l.value.logs.length);
        setLogHasMore(l.value.next_cursor !== null);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const cardStat = (section: ObserveSection): string | null => {
    if (section === "usage" && summary) {
      return `${fmt(summary.calls)} 次调用 · ${summary.success_rate === null ? "—" : `${(summary.success_rate * 100).toFixed(1)}%`} 成功`;
    }
    if (section === "alerts") {
      const firing = activeCount ?? 0;
      return `${firing} 条触发中`;
    }
    if (section === "trace") {
      return traceCount === null ? "—" : `${fmt(traceCount)}${traceHasMore ? "+" : ""} 个会话`;
    }
    if (section === "logs") {
      return logCount === null ? "—" : `${fmt(logCount)}${logHasMore ? "+" : ""} 条`;
    }
    return null;
  };

  return (
    <div className="max-w-[860px]">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-line pb-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[16px] font-semibold text-cream">观测</h1>
          <span className="text-[11.5px] text-tx2">可观测中心 · 按板块查看 · 数据来自 observability 模块</span>
        </div>
      </div>

      {/* 告警横幅：firing 中告警全局可见（30s 轮询，见 AlertPanel） */}
      <ActiveAlertBanner />

      {/* 健康摘要条：只放三个关键数字，其余进板块页 */}
      <div className="mt-4 grid grid-cols-3 gap-3">
        <div className="rounded-hard border border-line bg-panel px-4 py-3">
          <div className="eyebrow text-tx2">近 7 天调用</div>
          <div className="mt-1 font-mono text-[18px] leading-tight text-cream">
            {summary ? fmt(summary.calls) : "—"}
          </div>
        </div>
        <div className="rounded-hard border border-line bg-panel px-4 py-3">
          <div className="eyebrow text-tx2">成功率</div>
          <div className="mt-1 font-mono text-[18px] leading-tight text-cream">
            {summary && summary.success_rate !== null ? `${(summary.success_rate * 100).toFixed(1)}%` : "—"}
          </div>
        </div>
        <div className="rounded-hard border border-line bg-panel px-4 py-3">
          <div className="eyebrow text-tx2">活跃告警</div>
          <div className="mt-1 font-mono text-[18px] leading-tight text-cream">
            {activeCount === null ? "—" : activeCount}
          </div>
        </div>
      </div>

      {/* 板块卡片：一个功能域一张卡，点击跳转对应分类 */}
      <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-2">
        {SECTION_CARDS.map((card) => {
          const stat = cardStat(card.section);
          return (
            <button
              key={card.section}
              onClick={() => {
                window.location.hash = `#/observe/${card.section}`;
              }}
              className="group flex flex-col rounded-hard border border-line bg-panel px-4 py-3.5 text-left transition-colors hover:border-amber/50"
            >
              <div className="flex items-center gap-2">
                <span className="text-[15px] leading-none text-amber">{card.icon}</span>
                <span className="text-[13px] font-semibold text-cream">{card.title}</span>
                {card.status === "building" && (
                  <span className="ml-auto rounded-full border border-line px-2 py-0.5 text-[9.5px] text-tx3">
                    建设中
                  </span>
                )}
              </div>
              <p className="mt-1.5 text-[11.5px] leading-relaxed text-tx3">{card.desc}</p>
              <div className="mt-2.5 flex items-baseline justify-between">
                {stat ? (
                  <span className="font-mono text-[11px] text-tx2">{stat}</span>
                ) : (
                  <span className="text-[11px] text-tx3">{card.status === "building" ? "尚未接入数据源" : ""}</span>
                )}
                <span className="text-[11px] text-tx2 transition-colors group-hover:text-amber-hi">
                  进入 →
                </span>
              </div>
            </button>
          );
        })}
      </div>

      <p className="pt-5 text-[11px] leading-relaxed text-tx3">
        板块划分对照赛题可观测要求：<b className="text-tx2">推理轨迹</b>（Skill / MCP /
        Agent 会话）为赛题点名的全链路推理轨迹覆盖项，<b className="text-tx2">用量大盘</b>
        （Metrics）与<b className="text-tx2">日志</b>（Log）为数据类型覆盖，<b className="text-tx2">告警</b>
        为「在线监控与告警」场景。已实心板块的数据来自 RepoMesh 规划侧；
        执行侧 Agent 数据经「推理轨迹」板块接入（路线 1）。
      </p>
    </div>
  );
}
