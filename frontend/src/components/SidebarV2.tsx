import { useEffect, useRef, useState } from "react";
import type { Account } from "../api/auth";

/** v2 侧栏（CONS-40）：工作区切换器 → 新建 issue → 四导航 → 设置底锚 → 用户块。
 *  信息架构见 frontend-prototype/DESIGN-DECISION-V2.md §2、原型 redesign-issue-centric.html。
 *  诚实数据：工作区 = Organization，但组织读模型属 CONS-32（未接入）——
 *  当前只呈现登录账户，工作区列表与计数显「未接入」，不编造条目。 */

export type NavKey = "issues" | "repositories" | "teams" | "agents" | "settings";

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
 *  `border-left:2px solid var(--amber)`，v1 Sidebar 同款）；未选中留同宽透明边框防位移。 */
const navBase =
  "flex w-full items-center gap-2.5 rounded-hard border-l-2 px-2.5 py-[7px] text-left text-[13px]";

export function SidebarV2({
  account,
  nav,
  issueCount,
  onNavigate,
  onNewIssue,
  onLogout,
  onToast,
}: {
  account: Account;
  nav: NavKey;
  /** issue 导航计数；null = 数据源未提供（不显示计数，不编造） */
  issueCount: number | null;
  onNavigate: (nav: NavKey) => void;
  onNewIssue: () => void;
  onLogout: () => void;
  onToast: (text: string) => void;
}) {
  const [dropOpen, setDropOpen] = useState(false);
  const dropRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!dropOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (!dropRef.current?.contains(e.target as Node)) setDropOpen(false);
    };
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [dropOpen]);

  const initial = (account.display_name || account.username).slice(0, 1);

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
            <small className="block truncate text-[11px] text-tx2">工作区未接入 ▾</small>
          </div>
        </button>

        {dropOpen && (
          <div className="absolute top-[52px] left-0 z-20 w-[218px] rounded-hard border border-line bg-panel py-1.5 shadow-[0_16px_40px_rgba(0,0,0,0.6)]">
            <div className="flex items-center gap-2.5 px-2.5 pt-1 pb-2.5">
              <span className="grid size-[30px] flex-none place-items-center rounded-hard bg-[#4a4130] text-[12px] font-extrabold text-cream">
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
            <div className="px-2.5 pb-1.5 text-[11.5px] text-[#6b6046]">
              组织读模型未接入（CONS-32），暂无工作区列表
            </div>
            <button
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12.5px] text-tx2 hover:bg-amber/5 hover:text-tx"
              onClick={() => {
                setDropOpen(false);
                onToast("创建工作区 = 建组织并绑定 Org Leader，写路径未接入");
              }}
            >
              ＋ 创建工作区
            </button>
            <button
              className="flex w-full items-center gap-2 border-t border-line px-2.5 pt-2 pb-1 text-left text-[12.5px] text-salmon hover:bg-salmon/10"
              onClick={() => {
                setDropOpen(false);
                onLogout();
              }}
            >
              ［→ 退出登录
            </button>
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
            <b className="block truncate text-[12px] text-tx">{account.display_name || account.username}</b>
            <small className="block truncate text-[10.5px] text-tx2">
              {account.is_admin ? "管理员" : "本地账户"}
            </small>
          </div>
        </div>
      </div>
    </aside>
  );
}
