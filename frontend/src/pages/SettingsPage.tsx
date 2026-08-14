import { useCallback, useEffect, useState } from "react";
import type { Account } from "../api/auth";
import type {
  CodingAgentAdapterView,
  CodingAgentsProbe,
  ConsoleAgentView,
  RuntimeKind,
  SetupStatusView,
} from "../api/contract";
import { fetchConsoleAgents, gridSourceMode } from "../api/grid";
import { fetchCodingAgents, fetchSetupStatus } from "../api/platformSetup";
import { errText } from "../display";
import { useRuntimeRows } from "./useRuntimeRows";

/** 设置页（CONS-44，首版**只读**）。
 *
 *  原型这一页画的是「claude-code · 已配置 · CLI v2.1 · 4 个 worker 在用」这样的
 *  适配器卡片。那句「没有一项有数据源」在 main 合并之前是对的；`/setup/coding-agents`
 *  与 `/setup/status` 进来之后**读**的那半有源了（装没装、认没认得上、九项就绪检查），
 *  故本页现在画适配器清单。**仍然无源的两项照旧不画**：CLI 版本号（Controller 不
 *  回报）与「N 个 worker 在用」（探测结果不含按适配器的占用数）。
 *
 *  本页另外两件事沿用原样：
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

/** 九项检查的中文标签。后端返回的是机器名（`checks` 的键与 `next_actions` 的元素
 *  同一套），这里只做措辞，**不判定通过与否**——`ready_for_project_creation` 由
 *  服务端算，前端重算一遍就是第二套判定。 */
const CHECK_LABEL: Record<string, string> = {
  model: "模型连接",
  database: "数据库",
  agentteams: "AgentTeams",
  matrix: "Matrix 消息面",
  internal_auth: "内部凭据",
  github_app: "GitHub App",
  administrator: "管理员账号",
  agent_directory: "智能体花名册",
  repositories: "仓库 catalog",
};

/** 必检五项：与后端 `platform_setup.py` 的 `required` 元组同一份。**只用于分组
 *  呈现**（必检 / 选检），不参与就绪判定。 */
const REQUIRED_CHECKS = ["model", "database", "agentteams", "matrix", "internal_auth"];

const AUTH_LABEL: Record<CodingAgentAdapterView["auth_status"], string> = {
  authorized: "已授权",
  unauthorized: "未授权",
  // 「探不出来」不是「没认上」：合并这两态会把探测失败读成配置错误
  unknown: "无法判定",
};

function AdapterRow({ adapter }: { adapter: CodingAgentAdapterView }) {
  return (
    <div className="flex items-baseline gap-2.5 border-b border-panel py-2">
      <span className="w-[132px] flex-none text-[11.5px] text-tx">{adapter.display_name}</span>
      <span className="min-w-0 flex-1">
        <span className="block font-mono text-[11.5px] text-tx">
          {adapter.installed ? adapter.executable ?? "已安装" : "未安装"}
          {/* 未安装就没有「认没认上」这回事：auth 恒为 unknown，是
              binary_not_found 的必然结果而不是第二个事实。两个都写会让
              一份缺失读起来像两个问题。 */}
          {adapter.installed && (
            <span className="ml-2 text-tx2">· {AUTH_LABEL[adapter.auth_status]}</span>
          )}
        </span>
        <span className="mt-px block text-[10.5px] text-tx3">
          {/* detail 是探测原文（binary_not_found 这类），原样贴 */}
          {adapter.detail ? `${adapter.detail} · ` : ""}
          执行状态 {adapter.execution_status}
          {adapter.runnable_by_verified_driver ? "（可由已验证驱动执行）" : ""}
        </span>
      </span>
    </div>
  );
}

export function SettingsPage({ account }: { account: Account }) {
  const fetcher = useCallback((withRuntime: boolean) => fetchConsoleAgents(withRuntime), []);
  const { rows, error, phase, probeError } = useRuntimeRows<ConsoleAgentView>(fetcher);

  // 就绪检查与适配器探测各自取各自的：一个失败不该把另一个也变成空白。
  const [setup, setSetup] = useState<SetupStatusView | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [probe, setProbe] = useState<CodingAgentsProbe | null>(null);
  const [probeFailure, setProbeFailure] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchSetupStatus()
      .then((view) => !cancelled && setSetup(view))
      .catch((err: unknown) => !cancelled && setSetupError(errText(err)));
    fetchCodingAgents()
      .then((view) => !cancelled && setProbe(view))
      .catch((err: unknown) => !cancelled && setProbeFailure(errText(err)));
    return () => {
      cancelled = true;
    };
  }, []);

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

      <Section title="平台就绪">
        {setupError ? (
          <p className="pt-1 text-[11.5px] text-salmon">就绪检查取用失败：{setupError}</p>
        ) : setup === null ? (
          <p className="pt-1 text-[11.5px] text-tx3">检查中…</p>
        ) : (
          <>
            <Row
              label="可建项目"
              value={setup.ready_for_project_creation ? "是" : "否"}
              note={
                setup.ready_for_project_creation
                  ? "五项必检全通过（服务端判定，本页不重算）"
                  : // next_actions 混装必检与选检。照抄会把 GitHub App 这类
                    // 「这套部署没走 GitHub 交付」说成拦路项，所以这里只报
                    // 真正挡路的那几项，其余在徽标里以灰色示意。
                    `必检未过：${setup.next_actions
                      .filter((name) => REQUIRED_CHECKS.includes(name))
                      .map((name) => CHECK_LABEL[name] ?? name)
                      .join(" · ")}`
              }
            />
            <div className="flex flex-wrap gap-1.5 pt-2">
              {Object.entries(setup.checks).map(([name, passed]) => (
                <span
                  key={name}
                  className={`rounded-hard border px-2 py-px text-[11px] ${
                    passed
                      ? "border-olive text-olive"
                      : REQUIRED_CHECKS.includes(name)
                        ? "border-salmon text-salmon"
                        : "border-line text-tx3"
                  }`}
                  title={
                    REQUIRED_CHECKS.includes(name)
                      ? "必检项：不通过则无法建项目"
                      : "选检项：不参与可建项目判定"
                  }
                >
                  {CHECK_LABEL[name] ?? name}
                </span>
              ))}
            </div>
            <p className="pt-2 text-[11px] text-tx3">
              {/* 未通过的选检项用灰色而非红色：github_app 没配不是故障，
                  是这套部署没走 GitHub 交付。颜色区分必检与选检，措辞不喊错。 */}
              红=必检未过 · 绿=已过 · 灰=选检未过（不挡建项目）。账号 {setup.counts.accounts}{" "}
              · 智能体 {setup.counts.agents} · 仓库 {setup.counts.repositories}。
            </p>
          </>
        )}
      </Section>

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
          label="本地身份服务"
          value={`已登录 · ${account.username}`}
          note={`${account.is_admin ? "管理员" : "本地账户"} · 会话是 httpOnly cookie，前端不持有 token`}
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
              : "Controller 未回报任何 runtime_kind（探测不可达或未配置）。"}
          </p>
        )}
      </Section>

      <Section title="Coding Agent 适配器">
        {probeFailure ? (
          <p className="pt-1 text-[11.5px] text-salmon">适配器探测取用失败：{probeFailure}</p>
        ) : probe === null ? (
          <p className="pt-1 text-[11.5px] text-tx3">探测中…</p>
        ) : probe.adapters.length === 0 ? (
          <p className="pt-1 text-[11.5px] text-tx3">注册表里没有适配器清单。</p>
        ) : (
          <>
            {probe.adapters.map((adapter) => (
              <AdapterRow key={adapter.adapter_id} adapter={adapter} />
            ))}
            <p className="pt-2 text-[11px] text-tx3">
              {/* note 是后端原话，不改写：它划定了这份探测的适用范围 */}
              探测环境 {probe.environment}。{probe.note} 版本号与「N 个 worker 在用」
              仍无源，故不列。
            </p>
          </>
        )}
      </Section>

      <Section title="已知缺口">
        <ul className="grid gap-1.5 pt-1 text-[11.5px] text-tx2">
          <li>
            · 适配器<b className="text-tx2">写</b>路径（配置 / 接入 runtime）——二期，API 未立项。
            读已接入（见上方探测清单）。
          </li>
          <li>
            · 智能体<b className="text-tx2">醒睡态与运行时长</b>无源：Controller 不回报启动时间戳，
            期望态（DesiredRuntimeState）不是观测态。补齐路径在 AgentTeams 侧。
          </li>
          <li>· 房间刷新仍为轮询（5s）；SSE 推送需先定「哪些事实值得推」，另立项。</li>
          <li>
            · <b className="text-tx2">两套鉴权并存</b>：读模型 / 发现链 / 网格走共享动作 token，
            human_control 面（建团、审核台）走本地登录会话。同一控制台里谁认哪一套是按端点定的，
            尚未统一——统一到单一主体化凭据另立项。
          </li>
          <li>· 工作区（组织）删除与改名未接入；列表、切换与创建已接入（契约 v0.3 §2）。</li>
        </ul>
      </Section>
    </div>
  );
}
