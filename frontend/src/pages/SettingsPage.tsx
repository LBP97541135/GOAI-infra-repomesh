import { useCallback } from "react";
import type { ConsoleAgentView, RuntimeKind } from "../api/contract";
import { fetchConsoleAgents, gridSourceMode } from "../api/grid";
import { useRuntimeRows } from "./useRuntimeRows";

/** 设置页（CONS-44，首版**只读**）。
 *
 *  原型这一页画的是「claude-code · 已配置 · CLI v2.1 · 4 个 worker 在用」这样的
 *  适配器卡片——**没有一项有数据源**：适配器注册表 API 未立项（写路径二期），
 *  CLI 版本号 Controller 不回报。所以本页不画那个清单，改画**真实可得的两件事**：
 *
 *   1. **连接健康**：三条链路各自的真实状态。AgentTeams Controller 那条由
 *      `/console/agents` 的探测结果派生（可达 / 不可达 / 无事实各多少条）——
 *      这是全站唯一能观测 Controller 的地方，正好落在设置页的职责上；
 *   2. **缺口清单**：写路径与观测缺口逐条写明补齐路径，而不是留白。
 *
 *  运行时种类（`runtime_kind`）只在探测通时才有值，故按可得情况呈现，一条都没有
 *  就说「Controller 未回报」，不列一份看起来已配置好的假清单。 */

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex items-baseline gap-3 border-b border-panel py-2">
      <span className="w-[132px] flex-none text-[11.5px] text-tx2">{label}</span>
      <span className="min-w-0 flex-1">
        <span className="block font-mono text-[11.5px] text-tx">{value}</span>
        {note && <span className="mt-px block text-[10.5px] text-tx3">{note}</span>}
      </span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-5">
      <div className="eyebrow mb-1.5">{title}</div>
      {children}
    </section>
  );
}

export function SettingsPage() {
  const fetcher = useCallback((withRuntime: boolean) => fetchConsoleAgents(withRuntime), []);
  const { rows, error, phase, probeError } = useRuntimeRows<ConsoleAgentView>(fetcher);

  // 三态分别计数——合成一个「N 个健康」会把「没有这个资源」说成「不健康」
  const reachable = rows?.filter((a) => a.runtime !== null && a.runtime.reachable).length ?? 0;
  const unreachable = rows?.filter((a) => a.runtime !== null && !a.runtime.reachable).length ?? 0;
  const absent = rows?.filter((a) => a.runtime === null).length ?? 0;

  const kinds = [
    ...new Set(
      (rows ?? [])
        .map((a) => (a.runtime !== null && a.runtime.reachable ? a.runtime.runtime_kind : null))
        .filter((k): k is RuntimeKind => k !== null),
    ),
  ];

  const controllerValue =
    phase === "loading"
      ? "探测中…"
      : error
        ? "花名册取用失败，无法观测"
        : phase === "failed"
          ? "探测请求失败"
          : `可达 ${reachable} · 不可达 ${unreachable} · 无事实 ${absent}`;

  const controllerNote =
    phase === "failed" && probeError
      ? probeError.slice(0, 90)
      : unreachable > 0
        ? "不可达是契约规定的降级（HTTP 仍 200），持久化花名册不受影响，不等于团队故障"
        : "「无事实」= AgentTeams 未配置，或 Controller 报告没有这个资源（404）";

  const base = import.meta.env.VITE_API_BASE ?? "";

  return (
    <div className="max-w-[860px]">
      <div className="flex items-baseline gap-3 border-b border-line pb-3">
        <h1 className="text-[16px] font-semibold text-cream">设置</h1>
        <span className="microlabel">首版只读</span>
      </div>

      <Section title="连接健康">
        <Row
          label="AgentTeams Controller"
          value={controllerValue}
          note={rows === null && !error ? "等待花名册返回" : controllerNote}
        />
        <Row
          label="读模型 API"
          value={base === "" ? "同源（经 dev proxy /api）" : base}
          note={`鉴权：${import.meta.env.VITE_API_TOKEN ? "已配置 Bearer 动作 token" : "未配置 token"} · 当前数据源 ${gridSourceMode()}`}
        />
        <Row
          label="控制台身份"
          value="默认管理员 · 管理员"
          note="控制台不设登录门（裁决 2026-08-12），打开即以默认管理员进入；这是写死的呈现，不是某个真实账号。后端 /auth 端点仍保留供脚本使用，前端不再依赖，故此处不报它的健康。"
        />
      </Section>

      <Section title="Agent Runtime">
        {kinds.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {kinds.map((kind) => (
              <span key={kind} className="rounded-hard border border-bluegray px-2 py-px font-mono text-[11px] text-bluegray">
                {kind}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[11.5px] text-tx3">
            {phase === "loading"
              ? "探测中…"
              : "Controller 未回报任何 runtime_kind（探测不可达或未配置）。适配器的配置与接入是写路径，属二期能力——适配器注册表 API 尚未立项，本页不提供「配置」入口。"}
          </p>
        )}
      </Section>

      <Section title="已知缺口">
        <ul className="grid gap-1.5 pt-1 text-[11.5px] text-tx2">
          <li>· 适配器注册表写路径（配置 / 接入 runtime）——二期，API 未立项。</li>
          <li>
            · 智能体<b className="text-tx2">醒睡态与运行时长</b>无源：Controller 不回报启动时间戳，
            期望态（DesiredRuntimeState）不是观测态。补齐路径在 AgentTeams 侧。
          </li>
          <li>· 房间刷新仍为轮询（5s）；SSE 推送需先定「哪些事实值得推」，另立项。</li>
          <li>
            · 单用户：无登录门，读写端点一律用共享动作 token，控制台不区分操作者。
            多用户与按人鉴权、审计归属另立项。
          </li>
          <li>· 工作区（组织）删除与改名未接入；列表、切换与创建已接入（契约 v0.3 §2）。</li>
        </ul>
      </Section>
    </div>
  );
}
