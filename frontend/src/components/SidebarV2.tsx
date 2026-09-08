import { useEffect, useRef, useState } from "react";
import type { Account } from "../api/auth";
import type { OrganizationView } from "../api/contract";
import { errText } from "../display";
import {
  Activity,
  Bot,
  ChevronDown,
  FileCheck,
  FolderKanban,
  History,
  Inbox,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  Users,
  type LucideIcon,
} from "lucide-react";

/** v2 侧栏（CONS-40 → B-2 接线）＋ 2026-09-08 图标语言统一（用户三轮裁决）：
 *
 *  · 导航图标全量换 lucide 16px / 1.5 细线，自绘 SVG 退役；
 *  · 选中态＝圆角块（side-active 底 + 字重加重），左竖条语言退役；
 *  · 导航分「工作台 / 治理」两组（大写小节标题），历史决策与设置沉底；
 *  · 计数改 badge 胶囊（只展示真实数据，null 不显示）；
 *  · 侧栏可折叠成 64px 图标栏（悬停 tooltip）；
 *  · 工作区切换器 prompt 式双行（品牌方块 + REPOMESH + 当前工作区）；
 *  · 顶部搜索行接 ⌘K 命令面板（面板本体在 ConsoleShell）；
 *  · 「近期会话」列表按用户裁决整体移除（2026-09-08），会话入口收敛到
 *    issue 列表页与 ⌘K 搜索。
 *
 *  业务回路原样保留：工作区下拉/创建（A2 幂等键）、身份块、登出、
 *  回放模式提示。计数的 null 语义 = 数据源未提供，不显示 0 不编造。 */

export type NavKey =
  | "issues"
  | "reviews"
  | "repositories"
  | "teams"
  | "agents"
  | "observe"
  | "decision-chains"
  | "settings";

const NAV_ICON: Record<NavKey, LucideIcon> = {
  issues: Inbox,
  reviews: FileCheck,
  repositories: FolderKanban,
  teams: Users,
  agents: Bot,
  observe: Activity,
  "decision-chains": History,
  settings: Settings,
};

const NAV_LABEL: Record<NavKey, string> = {
  issues: "issue",
  reviews: "审核",
  repositories: "仓库",
  teams: "团队",
  agents: "智能体",
  observe: "观测",
  "decision-chains": "历史决策",
  settings: "设置",
};

/** 导航两组 + 底部区（历史决策/设置与身份块同区，prompt 的 bottom items 结构）。 */
const NAV_GROUPS: Array<{ heading: string; keys: NavKey[] }> = [
  { heading: "工作台", keys: ["issues", "reviews", "repositories"] },
  { heading: "治理", keys: ["teams", "agents", "observe"] },
];
const NAV_BOTTOM: NavKey[] = ["decision-chains", "settings"];

export function SidebarV2({
  account,
  nav,
  issueCount,
  reviewCount,
  workspaces,
  workspaceNote,
  selectedWorkspaceId,
  onSelectWorkspace,
  onCreateWorkspace,
  onNavigate,
  onNewIssue,
  onLogout,
  onToast,
  onOpenSearch,
}: {
  account: Account;
  nav: NavKey;
  /** issue 导航计数；null = 数据源未提供（不显示计数，不编造） */
  issueCount: number | null;
  /** 待办审核数；null = 取不到（未登录/流断），此时不显示计数而不是显示 0 */
  reviewCount: number | null;
  /** null = 不适用/取用失败（见 workspaceNote）；[] = 注册表为空 */
  workspaces: OrganizationView[] | null;
  workspaceNote: string | null;
  /** null = 全部工作区 */
  selectedWorkspaceId: string | null;
  onSelectWorkspace: (organizationId: string | null) => void;
  /** 创建回路（成功后由外层刷新列表并选中新工作区）；失败原因原样抛回。
   *  幂等键由本组件持有（A2：名称变化/成功才换键，重试沿用同键） */
  onCreateWorkspace: (name: string, idempotencyKey: string) => Promise<void>;
  onNavigate: (nav: NavKey) => void;
  onNewIssue: () => void;
  onLogout: () => void;
  onToast: (text: string) => void;
  /** 打开 ⌘K 命令面板（面板状态与快捷键监听在 ConsoleShell） */
  onOpenSearch?: () => void;
}) {
  const [dropOpen, setDropOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const dropRef = useRef<HTMLDivElement>(null);
  /** A2（§2.3）：键随逻辑创建走——名称一变/成功即换，重试沿用同键 */
  const createKeyRef = useRef<string>(crypto.randomUUID());

  const selected = workspaces?.find((w) => w.organization_id === selectedWorkspaceId) ?? null;
  const switcherLabel =
    workspaces === null ? (workspaceNote ?? "工作区未接入") : (selected?.name ?? "全部工作区");

  const submitCreate = () => {
    if (createSubmitting) return; // Enter 连击/键盘 repeat 不发第二次
    const name = createName.trim();
    if (!name) {
      onToast("请先输入工作区名称");
      return;
    }
    setCreateSubmitting(true);
    onCreateWorkspace(name, createKeyRef.current)
      .then(() => {
        setCreateName("");
        createKeyRef.current = crypto.randomUUID();
        setCreating(false);
        setDropOpen(false);
      })
      .catch((err: unknown) => {
        onToast(`创建失败：${errText(err)}`);
      })
      .finally(() => setCreateSubmitting(false));
  };

  useEffect(() => {
    if (!dropOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (!dropRef.current?.contains(e.target as Node)) setDropOpen(false);
    };
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [dropOpen]);

  const initial = (account.display_name || account.username).slice(0, 1);

  const countOf = (key: NavKey): number | null =>
    key === "issues" ? issueCount : key === "reviews" ? reviewCount : null;

  const NavButton = ({ item }: { item: NavKey }) => {
    const active = nav === item;
    const Icon = NAV_ICON[item];
    const count = countOf(item);
    return (
      <button
        title={collapsed ? NAV_LABEL[item] : undefined}
        className={`group flex w-full items-center gap-2.5 rounded-[6px] px-2.5 py-[7px] text-left text-[13px] select-none transition-colors ${
          collapsed ? "justify-center px-0" : ""
        } ${
          active
            ? "bg-side-active font-medium text-cream"
            : "text-tx2 hover:bg-side-active/50 hover:text-tx"
        }`}
        onClick={() => onNavigate(item)}
      >
        <Icon size={16} strokeWidth={1.5} className="flex-none" />
        {!collapsed && <span className="min-w-0 flex-1 truncate tracking-wide">{NAV_LABEL[item]}</span>}
        {!collapsed && count !== null && (
          <span className="flex h-5 min-w-[20px] flex-none items-center justify-center rounded-full bg-amber/10 px-1.5 font-mono text-[10px] font-medium text-amber-hi">
            {count}
          </span>
        )}
      </button>
    );
  };

  const GroupHeading = ({ text }: { text: string }) =>
    collapsed ? (
      <div className="mx-auto my-2 h-px w-6 bg-line" />
    ) : (
      <div className="microlabel mb-1 mt-3 px-2.5 first:mt-0">{text}</div>
    );

  return (
    <aside
      className={`scrollbar-none relative flex flex-none flex-col overflow-y-auto border-r border-line bg-side-rail px-3 pt-3.5 pb-3 transition-[width] duration-200 ${
        collapsed ? "w-[64px]" : "w-[236px]"
      }`}
    >
      <div ref={dropRef} className="relative">
        <button
          title={collapsed ? "REPOMESH · 工作区" : undefined}
          className={`flex w-full items-center rounded-[8px] px-1.5 py-1.5 text-left transition-colors hover:bg-side-active/50 ${
            collapsed ? "justify-center px-0" : "gap-2.5"
          }`}
          onClick={(e) => {
            e.stopPropagation();
            setDropOpen((v) => !v);
          }}
        >
          <span className="grid size-[32px] flex-none place-items-center rounded-[6px] bg-chalk font-mono text-[14px] font-semibold text-on-chalk">
            R
          </span>
          {!collapsed && (
            <span className="min-w-0 flex-1">
              <span className="block font-mono text-[12.5px] font-medium leading-none tracking-[0.14em] text-tx">
                REPOMESH
              </span>
              <span className="mt-1 block truncate text-[11px] leading-none text-tx2">{switcherLabel}</span>
            </span>
          )}
          {!collapsed && (
            <ChevronDown
              size={14}
              strokeWidth={1.5}
              className={`flex-none text-tx3 transition-transform ${dropOpen ? "rotate-180" : ""}`}
            />
          )}
        </button>

        {dropOpen && (
          <div className="absolute top-[52px] left-0 z-20 w-[218px] rounded-[8px] border border-line bg-side-panel py-1 shadow-float">
            <div className="flex items-center gap-2.5 px-2.5 pt-1 pb-2.5">
              <span className="grid size-[30px] flex-none place-items-center rounded-full bg-chip text-[12px] font-extrabold text-cream">
                {initial}
              </span>
              <div className="min-w-0">
                <div className="truncate text-[12.5px] text-tx">{account.display_name || account.username}</div>
                <div className="truncate font-mono text-[10.5px] text-tx2">
                  {account.username}
                  {account.is_admin ? " · ADMIN" : ""}
                </div>
              </div>
            </div>

            <div className="microlabel border-t border-line px-2.5 pt-2 pb-1">工作区</div>
            {workspaces === null && (
              <div className="px-2.5 pb-1.5 text-[11.5px] text-tx3">
                {workspaceNote ?? "工作区数据源不可用"}
              </div>
            )}
            {workspaces !== null && (
              <>
                <button
                  className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12.5px] ${
                    selectedWorkspaceId === null
                      ? "bg-side-active/50 font-medium text-tx"
                      : "text-tx2 hover:bg-side-active/50 hover:text-tx"
                  }`}
                  onClick={() => {
                    setDropOpen(false);
                    onSelectWorkspace(null);
                  }}
                >
                  全部工作区
                </button>
                {workspaces.length === 0 && (
                  <div className="px-2.5 pb-1.5 text-[11.5px] text-tx3">注册表暂无工作区</div>
                )}
                {workspaces.map((workspace) => (
                  <button
                    key={workspace.organization_id}
                    className={`flex w-full items-baseline gap-2 px-2.5 py-1.5 text-left text-[12.5px] ${
                      workspace.organization_id === selectedWorkspaceId
                        ? "bg-side-active/50 font-medium text-tx"
                        : "text-tx hover:bg-side-active/50"
                    }`}
                    onClick={() => {
                      setDropOpen(false);
                      onSelectWorkspace(workspace.organization_id);
                    }}
                  >
                    <span className="min-w-0 flex-1 truncate">{workspace.name}</span>
                    <span className="flex-none font-mono text-[10.5px] text-tx2">
                      {workspace.agent_count} agent
                    </span>
                  </button>
                ))}
                {!creating ? (
                  <button
                    className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12.5px] text-tx2 hover:bg-side-active/50 hover:text-tx"
                    onClick={() => setCreating(true)}
                  >
                    ＋ 创建工作区
                  </button>
                ) : (
                  <div className="px-2.5 py-1.5">
                    <input
                      autoFocus
                      className="w-full rounded-hard border border-line bg-transparent px-2 py-1 text-[12px] text-tx placeholder:text-tx3 focus:border-amber focus:outline-none"
                      placeholder="工作区名称"
                      value={createName}
                      disabled={createSubmitting}
                      onChange={(e) => {
                        setCreateName(e.target.value);
                        createKeyRef.current = crypto.randomUUID(); // A2：改名=新逻辑创建
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") submitCreate();
                        if (e.key === "Escape") setCreating(false);
                      }}
                    />
                    <div className="mt-1 flex items-center gap-2">
                      <button
                        className="rounded-hard bg-amber px-2 py-[3px] text-[11px] font-bold text-on-amber hover:bg-amber-hi disabled:opacity-60"
                        disabled={createSubmitting}
                        onClick={submitCreate}
                      >
                        {createSubmitting ? "创建中…" : "创建"}
                      </button>
                      <span className="text-[10px] text-tx3">建组织并登记 Org Leader（非运行时）</span>
                    </div>
                  </div>
                )}
              </>
            )}
            <button
              className="flex w-full items-center gap-2 border-t border-line px-2.5 pt-2 pb-1 text-left text-[12.5px] text-salmon hover:bg-salmon/10"
              onClick={() => {
                setDropOpen(false);
                onLogout();
              }}
            >
              退出登录
            </button>
          </div>
        )}
      </div>

      {/* ⌘K 搜索入口 + 新建 issue（折叠态各自变方块） */}
      <div className={`mt-3 flex flex-col gap-1.5 ${collapsed ? "items-center" : ""}`}>
        <button
          title={collapsed ? "搜索（Ctrl+K）" : undefined}
          className={`flex items-center gap-2 rounded-[6px] border border-line px-2 py-[6px] text-left text-tx3 transition-colors hover:border-tx3/40 hover:text-tx2 ${
            collapsed ? "w-8 justify-center border-transparent px-0 hover:bg-side-active/50" : "w-full"
          }`}
          onClick={onOpenSearch}
        >
          <Search size={14} strokeWidth={1.5} className="flex-none" />
          {!collapsed && <span className="flex-1 text-[12px]">搜索…</span>}
          {!collapsed && (
            <kbd className="rounded-[4px] border border-line bg-panel px-1 font-mono text-[9.5px] text-tx3">
              Ctrl K
            </kbd>
          )}
        </button>
        <button
          title={collapsed ? "新建 issue" : undefined}
          className={`flex w-full items-center justify-center gap-1.5 rounded-[8px] bg-amber py-[7px] text-[12.5px] font-extrabold tracking-[0.04em] text-on-amber transition-[filter] hover:brightness-105 ${
            collapsed ? "w-8 text-[15px] leading-none" : ""
          }`}
          onClick={onNewIssue}
        >
          {collapsed ? "+" : "+ 新建 issue"}
        </button>
      </div>

      <nav className={`mt-2 flex min-h-0 flex-1 flex-col overflow-y-auto scrollbar-none ${collapsed ? "mt-3 gap-2" : ""}`}>
        {NAV_GROUPS.map((group) => (
          <div key={group.heading} className="flex flex-col gap-0.5">
            <GroupHeading text={group.heading} />
            {group.keys.map((key) => (
              <NavButton key={key} item={key} />
            ))}
          </div>
        ))}

        {/* 「近期会话」列表已按用户裁决（2026-09-08）整体移除：
            会话入口收敛到 issue 列表页与 ⌘K 搜索，不在侧栏重复挂一份。 */}
      </nav>

      <div className="mt-auto grid gap-0.5 border-t border-line pt-2">
        {collapsed && <div className="mx-auto mb-2 h-px w-6 bg-line" />}
        {NAV_BOTTOM.map((key) => (
          <NavButton key={key} item={key} />
        ))}
        <div className={`flex items-center gap-2 px-2 py-1.5 ${collapsed ? "flex-col px-0" : ""}`}>
          <span
            className="grid size-7 flex-none place-items-center rounded-full bg-chip text-[12px] font-extrabold text-cream"
            title={collapsed ? account.display_name || account.username : undefined}
          >
            {initial}
          </span>
          {!collapsed && (
            <div className="min-w-0">
              <b className="block truncate text-[12px] text-tx">{account.display_name || account.username}</b>
              <small className="block truncate text-[10.5px] text-tx2">
                {account.is_admin ? "管理员" : "本地账户"}
              </small>
            </div>
          )}
        </div>
        <button
          title={collapsed ? "展开侧栏" : "收起侧栏"}
          className={`mt-0.5 grid h-7 place-items-center rounded-[6px] text-tx3 transition-colors hover:bg-side-active/50 hover:text-tx ${
            collapsed ? "w-full" : "w-7"
          }`}
          onClick={() => setCollapsed((v) => !v)}
        >
          {collapsed ? <PanelLeftOpen size={15} strokeWidth={1.5} /> : <PanelLeftClose size={15} strokeWidth={1.5} />}
        </button>
      </div>
    </aside>
  );
}
