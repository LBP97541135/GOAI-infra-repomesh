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
import { LocalAccountsPanel } from "../components/LocalAccountsPanel";
import { LocalCliPage } from "./LocalCliPage";
import { Bot, Info, Server, Settings2, SquareTerminal, Users, type LucideIcon } from "lucide-react";
import { errText } from "../display";
import { applyTheme, readStoredTheme, type ThemeName } from "../theme";
import { useRuntimeRows } from "./useRuntimeRows";

/** 设置页（CONS-44 · 2026-09-04 Trae 式重设计）。
 *
 *  布局为用户逐项确认的定稿：**左侧分类导航（lucide 图标 + 文字）+ 右侧紧凑 IDE
 *  风内容区**，设置行「左标签+说明小字 / 右控件或状态」，主题用下拉，无搜索框。
 *  六类：通用（主题·数据源）/ 账号与权限（新增账号表单按需展开）/ 平台（就绪+
 *  连接健康）/ 智能体（Runtime+适配器）/ 本地 CLI（原独立子页收编，侧栏入口撤除）/
 *  关于（运行信息；已知缺口清单按用户裁决移除）。
 *
 *  职责与红线沿袭旧版（这些不是样式，是契约）：
 *
 *   1. **诚实数据**：九项就绪检查「服务端判定，本页不重算」；探测原文（detail）
 *      原样贴；「无法判定」不合并进「未授权」；版本号与「N 个 worker 在用」仍无
 *      数据源，故不列。
 *   2. **写路径唯一**：本页唯一的服务端写路径是「账号与权限」里的新增本地账号
 *      （`components/LocalAccountsPanel.tsx`）；主题只写本机 localStorage。
 *   3. **连接健康**是全站唯一能观测 AgentTeams Controller 的地方（由
 *      `/console/agents` 探测结果派生），落在「平台」类。
 *   4. **已知缺口**收进「关于」：它是「诚实数据」文化的一部分，不与可操作设置
 *      混排，但不从控制台里消失。
 *
 *  取数不受分类切换影响：setup / 适配器探测 / 花名册在挂载时各取各的（一个失败
 *  不把另一个也变成空白），切到哪个分类都即时呈现。 */

type CategoryKey = "general" | "account" | "platform" | "agents" | "localcli" | "about";

/** 分类图标（lucide）：Trae 同款「图标 + 文字」导航项。 */
const CATEGORIES: { key: CategoryKey; label: string; icon: LucideIcon }[] = [
  { key: "general", label: "通用", icon: Settings2 },
  { key: "account", label: "账号与权限", icon: Users },
  { key: "platform", label: "平台", icon: Server },
  { key: "agents", label: "智能体", icon: Bot },
  { key: "localcli", label: "本地 CLI", icon: SquareTerminal },
  { key: "about", label: "关于", icon: Info },
];

/* ── Trae 式行与控件 ─────────────────────────────────────────────────────── */

/** 设置行：左「标题 + 说明小字」，右「控件或状态」。细分隔线、紧凑密度。 */
function SettingRow({
  title,
  note,
  children,
}: {
  title: React.ReactNode;
  note?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-6 border-b border-panel py-2.5 last:border-b-0">
      <div className="min-w-0">
        <p className="text-[12.5px] text-tx">{title}</p>
        {note != null && (
          <p className="mt-px max-w-[56ch] text-[11px] leading-relaxed text-tx3">{note}</p>
        )}
      </div>
      {children != null && <div className="flex-none">{children}</div>}
    </div>
  );
}

/** 右侧状态：色点 + 文案。idle（灰）是「选检未过 / 未安装」这类不是故障的态。 */
function StatusDot({ tone, label }: { tone: "ok" | "bad" | "idle" | "warn"; label: string }) {
  const dot = { ok: "bg-olive", bad: "bg-salmon", idle: "bg-line", warn: "bg-amber" }[tone];
  const text = { ok: "text-olive", bad: "text-salmon", idle: "text-tx3", warn: "text-amber" }[tone];
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-[11.5px] ${text}`}>
      <i className={`size-[7px] flex-none rounded-full not-italic ${dot}`} />
      {label}
    </span>
  );
}

/** Trae 同款右侧下拉：圆角、细边、聚焦琥珀。 */
function SelectControl({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="rounded-hard border border-line bg-well px-2.5 py-1.5 text-[12px] text-tx outline-none transition-colors hover:border-tx2 focus:border-amber"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

/** 内容区分类标题（右侧窗格顶部）。 */
function CategoryTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="pb-1 text-[13.5px] font-semibold text-cream">{children}</h2>;
}

/** 右侧长值（连接健康的探测计数之类），右对齐等宽。 */
function RowValue({ children }: { children: React.ReactNode }) {
  return <span className="block text-right font-mono text-[11.5px] leading-relaxed text-tx">{children}</span>;
}

/* ── 通用 ────────────────────────────────────────────────────────────────── */

const THEME_LABEL: Record<ThemeName, string> = {
  dark: "深色（默认）",
  light: "浅色",
};

/** 界面主题：唯一的非服务端设置，只写本机 localStorage（见 theme.ts），
 *  所以它不是本页的「写路径」。控件按定稿用 Trae 式下拉。 */
function GeneralCategory() {
  const [theme, setTheme] = useState<ThemeName>(readStoredTheme);
  const source = gridSourceMode();
  return (
    <>
      <CategoryTitle>通用</CategoryTitle>
      <SettingRow title="界面主题">
        <SelectControl
          value={theme}
          onChange={(value) => {
            const name = value as ThemeName;
            setTheme(name);
            applyTheme(name);
          }}
          options={(["dark", "light"] as const).map((name) => ({ value: name, label: THEME_LABEL[name] }))}
        />
      </SettingRow>
      <SettingRow title="数据源">
        <StatusDot
          tone={source === "live" ? "ok" : "warn"}
          label={source === "live" ? "live · 真实读模型" : "replay · 夹具回放"}
        />
      </SettingRow>
    </>
  );
}

/* ── 平台 ────────────────────────────────────────────────────────────────── */

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

function PlatformCategory({
  setup,
  setupError,
  account,
  base,
  onConfigure,
  controller,
}: {
  setup: SetupStatusView | null;
  setupError: string | null;
  account: Account;
  base: string;
  onConfigure: () => void;
  controller: { value: string; note: string | null; loading: boolean };
}) {
  const requiredChecks = new Set(
    setup?.dependencies.filter((dependency) => dependency.required).map((item) => item.id) ?? [],
  );
  const blocking = setup?.next_actions.filter((name) => requiredChecks.has(name)) ?? [];
  return (
    <>
      <CategoryTitle>平台</CategoryTitle>
      {setupError ? (
        <p className="py-2 text-[11.5px] text-salmon">就绪检查取用失败：{setupError}</p>
      ) : setup === null ? (
        <p className="py-2 text-[11.5px] text-tx3">检查中…</p>
      ) : (
        <>
          <SettingRow
            title="可建项目"
            note={
              setup.ready_for_project_creation
                ? "全部必检通过"
                : // next_actions 混装必检与选检。照抄会把 GitHub App 这类
                  // 「这套部署没走 GitHub 交付」说成拦路项，所以这里只报
                  // 真正挡路的那几项。
                  `必检未过：${blocking.map((name) => CHECK_LABEL[name] ?? name).join(" · ")}`
            }
          >
            <StatusDot
              tone={setup.ready_for_project_creation ? "ok" : "bad"}
              label={setup.ready_for_project_creation ? "就绪" : "未就绪"}
            />
          </SettingRow>
          {Object.entries(setup.checks).map(([name, passed]) => (
            <SettingRow key={name} title={CHECK_LABEL[name] ?? name}>
              {/* 未通过的选检项用灰而非红：github_app 没配不是故障，
                  是这套部署没走 GitHub 交付。颜色区分必检与选检。 */}
              <StatusDot
                tone={passed ? "ok" : requiredChecks.has(name) ? "bad" : "idle"}
                label={passed ? "已就绪" : requiredChecks.has(name) ? "必检未过" : "选检未过"}
              />
            </SettingRow>
          ))}
          <p className="pt-2 text-[11px] text-tx3">
            账号 {setup.counts.accounts} · 智能体 {setup.counts.agents} · 仓库 {setup.counts.repositories}。
          </p>
          {Object.values(setup.checks).some((passed) => !passed) ? (
            <button
              className="mt-3 rounded-hard border border-amber px-3 py-1.5 text-[11.5px] text-amber hover:bg-amber/10"
              onClick={onConfigure}
            >
              去配置
            </button>
          ) : null}
        </>
      )}

      <h3 className="pb-1 pt-5 text-[11px] font-semibold tracking-widest text-tx3 uppercase">连接健康</h3>
      <SettingRow title="AgentTeams Controller">
        {controller.loading ? (
          <span className="text-[11.5px] text-tx3">探测中…</span>
        ) : (
          <RowValue>{controller.value}</RowValue>
        )}
      </SettingRow>
      <SettingRow title="读模型 API">
        <RowValue>{base === "" ? "同源（经代理 /api）" : base}</RowValue>
      </SettingRow>
      <SettingRow title="本地身份服务">
        <RowValue>
          已登录 · {account.username} · {account.is_admin ? "管理员" : "本地账户"}
        </RowValue>
      </SettingRow>
    </>
  );
}

/* ── 智能体 ──────────────────────────────────────────────────────────────── */

const AUTH_LABEL: Record<CodingAgentAdapterView["auth_status"], string> = {
  authorized: "已授权",
  unauthorized: "未授权",
  // 「探不出来」不是「没认上」：合并这两态会把探测失败读成配置错误
  unknown: "无法判定",
};

function AdapterRow({ adapter }: { adapter: CodingAgentAdapterView }) {
  return (
    <SettingRow title={adapter.display_name}>
      {/* 未安装就没有「认没认上」这回事：auth 恒为 unknown，是
          binary_not_found 的必然结果而不是第二个事实。 */}
      <StatusDot
        tone={!adapter.installed ? "idle" : adapter.auth_status === "authorized" ? "ok" : adapter.auth_status === "unauthorized" ? "bad" : "idle"}
        label={adapter.installed ? AUTH_LABEL[adapter.auth_status] : "未安装"}
      />
    </SettingRow>
  );
}

function AgentsCategory({
  probe,
  probeFailure,
  kinds,
  agentsPhase,
  agentsError,
}: {
  probe: CodingAgentsProbe | null;
  probeFailure: string | null;
  kinds: RuntimeKind[];
  agentsPhase: string;
  agentsError: string | null;
}) {
  return (
    <>
      <CategoryTitle>智能体</CategoryTitle>
      <SettingRow
        title="运行时种类"
        note={agentsPhase === "loading" ? "探测中…" : undefined}
      >
        {kinds.length > 0 ? (
          <span className="flex flex-wrap justify-end gap-1.5">
            {kinds.map((kind) => (
              <span key={kind} className="rounded-hard border border-bluegray px-2 py-px font-mono text-[11px] text-bluegray">
                {kind}
              </span>
            ))}
          </span>
        ) : (
          <StatusDot tone="idle" label={agentsError ? "取用失败" : "无回报"} />
        )}
      </SettingRow>

      <h3 className="pb-1 pt-5 text-[11px] font-semibold tracking-widest text-tx3 uppercase">
        Coding Agent 适配器
      </h3>
      {probeFailure ? (
        <p className="py-2 text-[11.5px] text-salmon">适配器探测取用失败：{probeFailure}</p>
      ) : probe === null ? (
        <p className="py-2 text-[11.5px] text-tx3">探测中…</p>
      ) : probe.adapters.length === 0 ? (
        <p className="py-2 text-[11.5px] text-tx3">注册表里没有适配器清单。</p>
      ) : (
        <>
          {probe.adapters.map((adapter) => (
            <AdapterRow key={adapter.adapter_id} adapter={adapter} />
          ))}
        </>
      )}
    </>
  );
}

/* ── 关于 ────────────────────────────────────────────────────────────────── */

function AboutCategory({ account, base }: { account: Account; base: string }) {
  return (
    <>
      <CategoryTitle>关于</CategoryTitle>
      <SettingRow title="版本">
        <RowValue>{__APP_VERSION__}</RowValue>
      </SettingRow>
      <SettingRow title="数据源">
        <RowValue>{gridSourceMode() === "live" ? "live（真实读模型）" : "replay（夹具回放）"}</RowValue>
      </SettingRow>
      <SettingRow title="API 地址">
        <RowValue>{base === "" ? "同源（经代理 /api）" : base}</RowValue>
      </SettingRow>
      <SettingRow title="登录身份">
        <RowValue>
          {account.username} · {account.is_admin ? "管理员" : "本地账户"}
        </RowValue>
      </SettingRow>
    </>
  );
}

/* ── 页面骨架：左侧五类导航 + 右侧内容 ───────────────────────────────────── */

export function SettingsPage({
  account,
  onConfigure,
  initialCategory = "general",
}: {
  account: Account;
  onConfigure: () => void;
  /** 旧深链（#/settings/local-cli）落到对应分类；仅挂载时生效 */
  initialCategory?: CategoryKey;
}) {
  const [category, setCategory] = useState<CategoryKey>(initialCategory);
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

  const base = import.meta.env.VITE_API_BASE ?? "";

  return (
    <div className="flex items-start gap-8">
      {/* 左侧分类导航：滚动时钉在内容区顶部（Trae 的常驻导航栏） */}
      <nav className="w-[176px] flex-none">
        <div className="sticky top-5">
          <h1 className="pb-1 text-[16px] font-semibold text-cream">设置</h1>
          <ul className="grid gap-0.5 pt-2">
            {CATEGORIES.map((item) => {
              const Icon = item.icon;
              return (
                <li key={item.key}>
                  <button
                    type="button"
                    aria-current={category === item.key ? "true" : undefined}
                    onClick={() => setCategory(item.key)}
                    className={`flex w-full items-center gap-2.5 rounded-hard border-l-2 px-2.5 py-1.5 text-left transition-colors ${
                      category === item.key
                        ? "border-amber bg-well text-tx"
                        : "border-transparent text-tx2 hover:bg-well/60 hover:text-tx"
                    }`}
                  >
                    <Icon
                      className={`size-[15px] flex-none ${
                        category === item.key ? "text-amber" : "text-tx3"
                      }`}
                    />
                    <span className="text-[12.5px]">{item.label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </nav>

      {/* 右侧内容区：只渲染当前分类 */}
      <div className="min-w-0 max-w-[720px] flex-1 pb-6">
        {category === "general" && <GeneralCategory />}
        {category === "account" && (
          <>
            <CategoryTitle>账号与权限</CategoryTitle>
            <LocalAccountsPanel account={account} />
          </>
        )}
        {category === "platform" && (
          <PlatformCategory
            setup={setup}
            setupError={setupError}
            account={account}
            base={base}
            onConfigure={onConfigure}
            controller={{
              loading: rows === null && !error,
              value:
                phase === "loading"
                  ? "探测中…"
                  : error
                    ? "花名册取用失败，无法观测"
                    : phase === "failed"
                      ? "探测请求失败"
                      : `可达 ${reachable} · 不可达 ${unreachable} · 无事实 ${absent}`,
              note:
                phase === "failed" && probeError
                  ? probeError.slice(0, 90)
                  : unreachable > 0
                    ? "不可达是契约规定的降级（HTTP 仍 200），持久化花名册不受影响，不等于团队故障"
                    : "「无事实」= AgentTeams 未配置，或 Controller 报告没有这个资源（404）",
            }}
          />
        )}
        {category === "agents" && (
          <AgentsCategory
            probe={probe}
            probeFailure={probeFailure}
            kinds={kinds}
            agentsPhase={phase}
            agentsError={error}
          />
        )}
        {category === "localcli" && <LocalCliPage embedded />}
        {category === "about" && <AboutCategory account={account} base={base} />}
      </div>
    </div>
  );
}
