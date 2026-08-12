import { useEffect, useRef, useState } from "react";
import type { OrganizationView } from "../api/contract";
import { errText } from "../display";

/** v2 侧栏（CONS-40 → B-2 接线）：工作区切换器 → 新建 issue → 四导航 → 设置底锚 → 身份块。
 *  信息架构见 frontend-prototype/DESIGN-DECISION-V2.md §2、原型 redesign-issue-centric.html。
 *
 *  身份块：控制台无登录门（裁决 2026-08-12），没有会话也就没有「当前账号」这个
 *  事实，故如实写死「默认管理员」——不冒充某个真实账号，也不留退出登录的死按钮。
 *
 *  工作区 = Organization（契约 v0.3 §2 注册表）。`workspaces === null` 表示
 *  「数据源不适用或取用失败」（文案由 workspaceNote 说明），与空列表 []（注册表
 *  真的没有条目）是两个态，不合并。创建工作区 = 建组织 + 登记 Org Leader——
 *  leader 是期望态登记行，不是已拉起的运行时（§2.3 诚实边界）。 */

export type NavKey = "issues" | "repositories" | "teams" | "agents" | "settings";

/** 无登录门下唯一的身份呈现（单用户本地部署）。多用户是另立项的能力。 */
const IDENTITY_NAME = "默认管理员";
const IDENTITY_ROLE = "管理员";

const NAV_ITEMS: Array<{ key: NavKey; label: string }> = [
  { key: "issues", label: "issue" },
  { key: "repositories", label: "仓库" },
  { key: "teams", label: "团队" },
  { key: "agents", label: "智能体" },
];

function NavIcon({ nav }: { nav: NavKey }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const };
  if (nav === "issues")
    return (
      <svg viewBox="0 0 24 24" {...common}>
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    );
  if (nav === "repositories")
    return (
      <svg viewBox="0 0 24 24" {...common}>
        <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      </svg>
    );
  if (nav === "teams")
    return (
      <svg viewBox="0 0 24 24" {...common}>
        <circle cx="8" cy="9" r="3" />
        <circle cx="16" cy="9" r="3" />
        <path d="M2 20v-1a5 5 0 0 1 6-4.9M22 20v-1a5 5 0 0 0-6-4.9" />
      </svg>
    );
  if (nav === "agents")
    return (
      <svg viewBox="0 0 24 24" {...common}>
        <rect x="4" y="7" width="16" height="12" rx="2" />
        <path d="M12 4v3M9 12h.01M15 12h.01M9.5 16h5" />
      </svg>
    );
  return (
    <svg viewBox="0 0 24 24" {...common}>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 3.5v2M12 18.5v2M20.5 12h-2M5.5 12h-2M17.9 6.1l-1.4 1.4M7.5 16.5l-1.4 1.4M17.9 17.9l-1.4-1.4M7.5 7.5L6.1 6.1" />
    </svg>
  );
}

/** 选中态的琥珀左竖条是 Variant D 的导航语言（原型 `.nav.active` 的
 *  `border-left:2px solid var(--amber)`）；未选中留同宽透明边框防位移。 */
const navBase =
  "flex w-full items-center gap-2.5 rounded-hard border-l-2 px-2.5 py-[7px] text-left text-[13px]";

export function SidebarV2({
  nav,
  issueCount,
  workspaces,
  workspaceNote,
  selectedWorkspaceId,
  onSelectWorkspace,
  onCreateWorkspace,
  onNavigate,
  onNewIssue,
  onToast,
}: {
  nav: NavKey;
  /** issue 导航计数；null = 数据源未提供（不显示计数，不编造） */
  issueCount: number | null;
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
  onToast: (text: string) => void;
}) {
  const [dropOpen, setDropOpen] = useState(false);
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

  const initial = IDENTITY_NAME.slice(0, 1);

  return (
    <aside className="relative flex w-[236px] flex-none flex-col overflow-y-auto border-r border-line bg-ink-deep px-3 pt-3.5 pb-3">
      <div ref={dropRef} className="relative">
        <button
          className="flex w-full items-center gap-2.5 rounded-hard px-1.5 py-1.5 text-left hover:bg-amber/5"
          onClick={(e) => {
            e.stopPropagation();
            setDropOpen((v) => !v);
          }}
        >
          <span className="grid size-[30px] flex-none place-items-center rounded-hard bg-amber font-mono text-[14px] font-extrabold text-[#16120a]">
            R
          </span>
          <div className="min-w-0">
            <strong className="block font-mono text-[12.5px] tracking-[0.14em] text-cream">REPOMESH</strong>
            <small className="block truncate text-[11px] text-tx2">{switcherLabel} ▾</small>
          </div>
        </button>

        {dropOpen && (
          <div className="absolute top-[52px] left-0 z-20 w-[218px] rounded-hard border border-line bg-panel py-1.5 shadow-[0_16px_40px_rgba(0,0,0,0.6)]">
            <div className="flex items-center gap-2.5 px-2.5 pt-1 pb-2.5">
              <span className="grid size-[30px] flex-none place-items-center rounded-hard bg-[#4a4130] text-[12px] font-extrabold text-cream">
                {initial}
              </span>
              <div className="min-w-0">
                <div className="truncate text-[12.5px] text-tx">{IDENTITY_NAME}</div>
                <div className="truncate font-mono text-[10.5px] text-tx2">本地部署 · 无登录门</div>
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
                    selectedWorkspaceId === null ? "text-amber-hi" : "text-tx2 hover:bg-amber/5 hover:text-tx"
                  }`}
                  onClick={() => {
                    setDropOpen(false);
                    onSelectWorkspace(null);
                  }}
                >
                  ◎ 全部工作区
                </button>
                {workspaces.length === 0 && (
                  <div className="px-2.5 pb-1.5 text-[11.5px] text-tx3">注册表暂无工作区</div>
                )}
                {workspaces.map((workspace) => (
                  <button
                    key={workspace.organization_id}
                    className={`flex w-full items-baseline gap-2 px-2.5 py-1.5 text-left text-[12.5px] ${
                      workspace.organization_id === selectedWorkspaceId
                        ? "text-amber-hi"
                        : "text-tx hover:bg-amber/5"
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
                    className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12.5px] text-tx2 hover:bg-amber/5 hover:text-tx"
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
                        className="rounded-hard bg-amber px-2 py-[3px] text-[11px] font-bold text-[#191308] hover:bg-amber-hi disabled:opacity-60"
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
          </div>
        )}
      </div>

      <button
        className="mt-3 mb-1 flex w-full items-center justify-center gap-1.5 rounded-hard bg-amber py-[7px] text-[12.5px] font-extrabold tracking-[0.04em] text-[#191308] hover:bg-amber-hi"
        onClick={onNewIssue}
      >
        + 新建 issue
      </button>

      <nav className="mt-2 grid gap-0.5">
        {NAV_ITEMS.map((item) => {
          const active = nav === item.key;
          const count = item.key === "issues" ? issueCount : null;
          return (
            <button
              key={item.key}
              className={
                active
                  ? `${navBase} border-amber bg-amber/10 text-amber-hi`
                  : `${navBase} border-transparent text-tx hover:bg-amber/5 hover:text-amber-hi`
              }
              onClick={() => onNavigate(item.key)}
            >
              <span className={`grid size-4 flex-none place-items-center ${active ? "text-amber-hi" : "text-amber"} [&_svg]:size-4`}>
                <NavIcon nav={item.key} />
              </span>
              {item.label}
              {count !== null && (
                <span className="ml-auto rounded-hard bg-line px-1.5 font-mono text-[10.5px] text-tx2">{count}</span>
              )}
            </button>
          );
        })}
      </nav>

      <div className="mt-auto grid gap-0.5 border-t border-line pt-2">
        <button
          className={
            nav === "settings"
              ? `${navBase} border-amber bg-amber/10 text-amber-hi`
              : `${navBase} border-transparent text-tx hover:bg-amber/5 hover:text-amber-hi`
          }
          onClick={() => onNavigate("settings")}
        >
          <span className={`grid size-4 flex-none place-items-center ${nav === "settings" ? "text-amber-hi" : "text-amber"} [&_svg]:size-4`}>
            <NavIcon nav="settings" />
          </span>
          设置
        </button>
        <div className="flex items-center gap-2 px-2 py-1.5">
          <span className="grid size-7 flex-none place-items-center rounded-hard bg-[#4a4130] text-[12px] font-extrabold text-cream">
            {initial}
          </span>
          <div className="min-w-0">
            <b className="block truncate text-[12px] text-tx">{IDENTITY_NAME}</b>
            <small className="block truncate text-[10.5px] text-tx2">{IDENTITY_ROLE}</small>
          </div>
        </div>
      </div>
    </aside>
  );
}
