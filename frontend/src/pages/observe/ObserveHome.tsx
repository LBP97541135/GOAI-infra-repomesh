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

/** 入口选择的本机记忆：上一次选了哪扇门，下次直接落进去（选择器仍常驻可切）。 */
const OBSERVE_SURFACE_KEY = "repomesh-observe-surface";

type ObserveSurface = "choose" | "local";

function storedSurface(): ObserveSurface {
  try {
    return localStorage.getItem(OBSERVE_SURFACE_KEY) === "local" ? "local" : "choose";
  } catch {
    return "choose";
  }
}

function savedAgentloopUrl(): string {
  try {
    return (localStorage.getItem(AGENTLOOP_URL_KEY) ?? "").trim();
  } catch {
    return "";
  }
}

/** 观测中心门户（#/observe）。
 *
 * **入口即选择**（用户定稿）：进观测先见两扇门——
 *  - 「自研观测」：进入本地四大板块（推理轨迹 / 用量 / 日志 / 告警），数据来自
 *    observability 模块的读模型；
 *  - 「AgentLoop」：直接跳出阿里云云端控制台（span 全链路 / 时序指标 / 长期留存），
 *    地址由服务端从部署既有 OTLP 配置推导，用户手改只存本机。
 *
 * 选择记进 localStorage，下次直落上次的门；顶部入口条常驻（两个 pill），随时切回。
 * 告警横幅全局可见（不分入口）。摘要条只在本地面拉一次（门户不需要 30s 轮询）。 */

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
  const [surface, setSurface] = useState<ObserveSurface>(storedSurface);
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

  const enterSurface = (next: ObserveSurface) => {
    setSurface(next);
    try {
      localStorage.setItem(OBSERVE_SURFACE_KEY, next);
    } catch {
      // 隐私模式存不进去就当会话内选择
    }
  };

  useEffect(() => {
    if (surface !== "local") return;
    let cancelled = false;
    // 本地面只做一次摘要快照；各路独立降级——某一路端点失败不拖垮其余数字
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
  }, [surface]);

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

  /** 入口条：两扇门的常驻切换器（进入任一面后收成一行 pill，随时切回/换门）。 */
  const surfaceSwitch = (
    <div className="flex items-center gap-2">
      <span className="microlabel">入口</span>
      <button
        onClick={() => enterSurface("local")}
        className={`rounded-full border px-3 py-[3px] text-[11px] transition-colors ${
          surface === "local"
            ? "border-amber bg-amber/10 text-amber"
            : "border-line text-tx2 hover:border-amber/50 hover:text-tx"
        }`}
      >
        自研 · 本地
      </button>
      <button
        onClick={openAgentloop}
        className="rounded-full border border-line px-3 py-[3px] text-[11px] text-tx2 transition-colors hover:border-amber/50 hover:text-tx"
        title={agentloopJumpUrl ? "新窗口打开 AgentLoop 控制台" : "首次点击进行配置"}
      >
        AgentLoop ↗
      </button>
    </div>
  );

  return (
    <div className="max-w-[860px]">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-line pb-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[16px] font-semibold text-cream">观测</h1>
          <span className="text-[11.5px] text-tx2">可观测中心 · 数据来自 observability 模块与 AgentLoop</span>
        </div>
        {surfaceSwitch}
      </div>

      <ActiveAlertBanner />

      {/* ═══ 入口选择：两扇门 ═══ */}
      {surface === "choose" && (
        <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2">
          <button
            onClick={() => enterSurface("local")}
            className="group flex flex-col rounded-hard border border-line bg-panel px-5 py-5 text-left transition-colors hover:border-amber"
          >
            <div className="flex items-center gap-2.5">
              <span className="text-[20px] leading-none text-amber">◎</span>
              <span className="text-[15px] font-bold text-cream">自研观测 · 本地</span>
            </div>
            <p className="mt-2 text-[12px] leading-relaxed text-tx2">
              进入本地四大板块：推理轨迹 / 用量大盘 / 日志 / 告警。数据来自 RepoMesh
              observability 模块读模型，按 Issue 归因。
            </p>
            <div className="mt-3 flex items-baseline justify-between">
              <span className="font-mono text-[11px] text-tx2">4 个板块已就绪</span>
              <span className="text-[11.5px] text-tx2 transition-colors group-hover:text-amber-hi">进入 →</span>
            </div>
          </button>
          <button
            onClick={openAgentloop}
            className="group flex flex-col rounded-hard border border-line bg-panel px-5 py-5 text-left transition-colors hover:border-amber"
          >
            <div className="flex items-center gap-2.5">
              <span className="text-[20px] leading-none text-amber">⛓</span>
              <span className="text-[15px] font-bold text-cream">AgentLoop · 阿里云</span>
            </div>
            <p className="mt-2 text-[12px] leading-relaxed text-tx2">
              跳转云端控制台：span 全链路瀑布 / 时序指标趋势 / 长期留存。全量遥测已从本部署同步上报。
            </p>
            <div className="mt-3 flex items-baseline justify-between">
              <span className="font-mono text-[11px] text-tx2">
                {agentloopJumpUrl
                  ? agentloop?.region
                    ? `已连接 · ${agentloop.region}`
                    : "已连接"
                  : "未配置 · 首次点击进行配置"}
              </span>
              <span className="text-[11.5px] text-tx2 transition-colors group-hover:text-amber-hi">新窗口跳转 ↗</span>
            </div>
          </button>
        </div>
      )}

      {/* ═══ 自研面：健康摘要 + 四大板块 ═══ */}
      {surface === "local" && (
        <>
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
          <div className="eyebrow mt-5">四大板块</div>
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

          <p className="pt-5 text-[11px] leading-relaxed text-tx3">
            板块划分对照赛题可观测要求：<b className="text-tx2">推理轨迹</b>（Skill / MCP /
            Agent 会话）为赛题点名的全链路推理轨迹覆盖项，<b className="text-tx2">用量大盘</b>
            （Metrics）与<b className="text-tx2">日志</b>（Log）为数据类型覆盖，<b className="text-tx2">告警</b>
            为「在线监控与告警」场景。已实心板块的数据来自 RepoMesh 规划侧；
            执行侧 Agent 数据经「推理轨迹」板块接入（路线 1）。
          </p>
        </>
      )}

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
    </div>
  );
}
