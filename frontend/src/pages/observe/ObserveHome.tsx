import { useEffect, useState } from "react";
import { defaultClient } from "../../api/client";
import type { ObserveSummary } from "../../api/contract";
import { fetchAgentloopConfig, type AgentLoopConfig } from "../../api/agentloop";
import type { ObserveSection } from "../../routes";
import { ActiveAlertBanner } from "../../components/AlertPanel";
import { Modal } from "../../components/Modal";

const fmt = (n: number) => n.toLocaleString("en-US");

/** AgentLoop 跳转地址的本机记忆键。配置弹层里用户手动改过的地址只存这里，
 *  不回传服务端——服务端推导（OTLP 配置）覆盖不了的个性化兜底。 */
const AGENTLOOP_URL_KEY = "repomesh-agentloop-url";

function savedAgentloopUrl(): string {
  try {
    return (localStorage.getItem(AGENTLOOP_URL_KEY) ?? "").trim();
  } catch {
    return "";
  }
}

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
  // AgentLoop 跳转配置：服务端从部署既有 OTLP 配置推导；拿不到（含旧后端
  // 无此端点）不报错，卡片点击时走配置弹层。
  const [agentloop, setAgentloop] = useState<AgentLoopConfig | null>(null);
  const [agentloopDialog, setAgentloopDialog] = useState(false);
  const [dialogUrl, setDialogUrl] = useState("");
  const [savedUrl, setSavedUrl] = useState(savedAgentloopUrl);

  useEffect(() => {
    let cancelled = false;
    fetchAgentloopConfig()
      .then((config) => !cancelled && setAgentloop(config))
      .catch(() => !cancelled && setAgentloop(null));
    return () => {
      cancelled = true;
    };
  }, []);

  const agentloopJumpUrl = savedUrl || agentloop?.console_url || null;

  const openAgentloop = () => {
    const url = agentloopJumpUrl;
    if (url) {
      window.open(url, "_blank", "noopener");
      return;
    }
    setDialogUrl(agentloop?.console_url ?? "");
    setAgentloopDialog(true);
  };

  const saveAndEnter = () => {
    const url = dialogUrl.trim();
    if (!url) return;
    try {
      localStorage.setItem(AGENTLOOP_URL_KEY, url);
    } catch {
      // 存不进去（隐私模式）本次仍可直接进，只是下次要再填一遍
    }
    setSavedUrl(url);
    setAgentloopDialog(false);
    window.open(url, "_blank", "noopener");
  };

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

      {/* 本地板块：自研读模型的四个功能域，日常观测的主线 */}
      <div className="eyebrow mt-5">本地</div>
      <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">
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

      {/* AgentLoop：云端技术信号（span 全链路 / 时序指标 / 长期留存）。
          地址由服务端从部署既有 OTLP 配置推导，用户手改只存本机。 */}
      <div className="eyebrow mt-5">AgentLoop</div>
      <button
        onClick={openAgentloop}
        className="group mt-2 flex w-full flex-col rounded-hard border border-line bg-panel px-4 py-3.5 text-left transition-colors hover:border-amber/50"
      >
        <div className="flex items-center gap-2">
          <span className="text-[15px] leading-none text-amber">⛓</span>
          <span className="text-[13px] font-semibold text-cream">全链路 · AgentLoop</span>
          <span className="ml-auto text-[11px] text-tx2 transition-colors group-hover:text-amber-hi">
            新窗口进入 ↗
          </span>
        </div>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-tx3">
          云端技术信号 · 调用链瀑布 / 指标趋势 / 长期留存（全量遥测已同步上报）
        </p>
        <div className="mt-2.5 flex items-baseline justify-between">
          <span className="font-mono text-[11px] text-tx2">
            {agentloopJumpUrl
              ? agentloop?.region
                ? `已连接 · ${agentloop.region}`
                : "已连接"
              : "未配置 · 首次点击进行配置"}
          </span>
          {agentloopJumpUrl && (
            <span
              role="button"
              tabIndex={0}
              className="text-[11px] text-tx3 hover:text-tx"
              onClick={(e) => {
                e.stopPropagation();
                setDialogUrl(agentloopJumpUrl);
                setAgentloopDialog(true);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.stopPropagation();
                  setDialogUrl(agentloopJumpUrl);
                  setAgentloopDialog(true);
                }
              }}
            >
              修改地址
            </span>
          )}
        </div>
      </button>

      {/* 一次性配置弹层：只在跳转地址缺失或用户主动改地址时出现 */}
      <Modal
        open={agentloopDialog}
        className="m-auto w-[min(520px,92vw)] rounded-hard border border-line-strong bg-panel p-0 text-tx shadow-pop"
        onClose={() => setAgentloopDialog(false)}
      >
        <div className="px-6 py-5">
          <div className="eyebrow mb-1.5">接入 AgentLoop</div>
          <h2 className="text-[15px] font-semibold text-cream">AgentLoop 控制台地址</h2>
          <p className="mt-1.5 text-[11.5px] leading-[1.7] text-tx2">
            {agentloop === null
              ? "当前部署没能自动推导出控制台地址。粘贴你的 AgentLoop 控制台地址，保存后本机记住、以后一键直达。"
              : "已按部署配置自动推导，通常无需修改；如有出入可粘贴控制台地址覆盖（只存本机）。"}
          </p>
          <input
            className="mt-3 w-full rounded-hard border border-line bg-well px-2.5 py-1.5 font-mono text-[11.5px] text-tx outline-none focus:border-amber"
            placeholder="https://arms.console.aliyun.com/?regionId=cn-hangzhou"
            value={dialogUrl}
            onChange={(e) => setDialogUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") saveAndEnter();
            }}
            autoFocus
          />
          <div className="mt-4 flex justify-end gap-2">
            <button
              className="rounded-hard border border-line-strong bg-panel px-3 py-1.5 text-[11.5px] text-tx2 hover:border-amber hover:text-tx"
              onClick={() => setAgentloopDialog(false)}
            >
              取消
            </button>
            <button
              className="rounded-hard bg-amber px-3 py-1.5 text-[11.5px] font-extrabold text-on-amber hover:bg-amber-hi disabled:cursor-not-allowed disabled:opacity-40"
              disabled={dialogUrl.trim() === ""}
              onClick={saveAndEnter}
            >
              保存并进入
            </button>
          </div>
        </div>
      </Modal>

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
