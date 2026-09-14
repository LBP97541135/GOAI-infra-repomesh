import { useEffect, useMemo, useRef, useState } from "react";
import { FileText, PanelLeft, Search, Settings, X } from "lucide-react";
import type { IssueListItemView } from "../api/contract";
import { PHASE_SKIN, PHASE_SKIN_FALLBACK } from "../display";

/** CommandPalette：⌘K/Ctrl+K 命令面板（2026-09-08 集成，用户裁决Scope：
 *  本地过滤——issue 标题 + 页面跳转，不接后端搜索）。
 *
 *  交互：Ctrl/⌘+K 或侧栏搜索行唤起；输入即时过滤；↑↓ 选择、Enter 执行、
 *  Esc 关闭；分组「页面 / 会话」。纯前端过滤（标题包含，大小写不敏感），
 *  数据由 ConsoleShell 传入（已有轮询，不额外发请求）。
 *
 *  样式对齐侧栏新语言：panel 面板、line 边框、rounded-[10px]、lucide 细线。 */

type NavGo = (nav: "issues" | "reviews" | "repositories" | "teams" | "agents" | "observe" | "decision-chains" | "settings") => void;

const PAGES: Array<{ label: string; nav: Parameters<NavGo>[0]; icon: typeof PanelLeft }> = [
  { label: "issue 列表", nav: "issues", icon: PanelLeft },
  { label: "审核队列", nav: "reviews", icon: PanelLeft },
  { label: "仓库", nav: "repositories", icon: PanelLeft },
  { label: "团队", nav: "teams", icon: PanelLeft },
  { label: "智能体", nav: "agents", icon: PanelLeft },
  { label: "观测", nav: "observe", icon: PanelLeft },
  { label: "历史决策", nav: "decision-chains", icon: PanelLeft },
  { label: "设置", nav: "settings", icon: Settings },
];

type Row =
  | { kind: "page"; key: string; label: string; nav: Parameters<NavGo>[0] }
  | { kind: "issue"; key: string; label: string; note: string; issueId: string };

export function CommandPalette({
  open,
  onClose,
  issues,
  onNavigate,
  onOpenIssue,
  onNewIssue,
}: {
  open: boolean;
  onClose: () => void;
  /** 已加载的 issue 列表（ConsoleShell 轮询数据原样传入；null = 尚未取到） */
  issues: IssueListItemView[] | null;
  onNavigate: (nav: "issues" | "reviews" | "repositories" | "teams" | "agents" | "observe" | "decision-chains" | "settings") => void;
  onOpenIssue: (issueId: string) => void;
  onNewIssue?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  const rows = useMemo<Row[]>(() => {
    const q = query.trim().toLowerCase();
    const pageRows: Row[] = PAGES.filter((p) => !q || p.label.toLowerCase().includes(q)).map((p) => ({
      kind: "page" as const,
      key: `page:${p.nav}`,
      label: p.label,
      nav: p.nav,
    }));
    if (!q) {
      // 空查询：页面 + 「新建 issue」快捷动作，不整列刷屏
      const base: Row[] = pageRows;
      return onNewIssue ? [{ kind: "page", key: "action:new", label: "新建 issue", nav: "issues" }, ...base] : base;
    }
    const issueRows: Row[] = (issues ?? [])
      .filter(
        (i) =>
          i.title.toLowerCase().includes(q) ||
          (i.issue_key ?? "").toLowerCase().includes(q),
      )
      .slice(0, 8)
      .map((i) => ({
        kind: "issue" as const,
        key: `issue:${i.issue_id}`,
        label: i.title,
        note: i.phase_note,
        issueId: i.issue_id,
      }));
    return [...pageRows, ...issueRows];
  }, [query, issues, onNewIssue]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setCursor(0);
      // 等挂载后一拍再聚焦（面板有淡入）
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => setCursor(0), [query]);

  const run = (row: Row | undefined) => {
    if (!row) return;
    if (row.kind === "page") {
      if (row.key === "action:new") onNewIssue?.();
      else onNavigate(row.nav);
    } else {
      onOpenIssue(row.issueId);
    }
    onClose();
  };

  if (!open) return null;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(rows[cursor]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-ink/40 px-4 pt-[15vh] backdrop-blur-[2px]">
      <div className="absolute inset-0" onClick={onClose} />
      <div className="relative w-full max-w-xl overflow-hidden rounded-[10px] border border-line bg-panel shadow-pop">
        <div className="flex items-center border-b border-line px-4">
          <Search size={16} strokeWidth={1.5} className="mr-3 flex-none text-tx3" />
          <input
            ref={inputRef}
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            className="flex-1 bg-transparent py-3.5 text-[13.5px] text-tx outline-none placeholder:text-tx3"
            placeholder="搜索 issue、跳转页面…"
          />
          <button
            onClick={onClose}
            title="关闭（Esc）"
            className="ml-3 grid size-6 place-items-center rounded-[6px] text-tx3 transition-colors hover:bg-side-active/60 hover:text-tx"
          >
            <X size={14} strokeWidth={1.5} />
          </button>
        </div>

        <div ref={listRef} className="max-h-[46vh] overflow-y-auto p-1.5">
          {rows.length === 0 && (
            <div className="flex flex-col items-center justify-center py-10">
              <FileText size={22} strokeWidth={1.5} className="mb-2 text-tx3" />
              <p className="text-[13px] text-tx2">没有匹配的结果</p>
            </div>
          )}
          {rows.map((row, i) => {
            const activeRow = i === cursor;
            return (
              <button
                key={row.key}
                className={`flex w-full items-center gap-2.5 rounded-[6px] px-2.5 py-2 text-left text-[13px] ${
                  activeRow ? "bg-side-active text-tx" : "text-tx2 hover:bg-side-active/40"
                }`}
                onMouseEnter={() => setCursor(i)}
                onClick={() => run(row)}
              >
                {row.kind === "page" ? (
                  <PanelLeft size={14} strokeWidth={1.5} className="flex-none text-tx3" />
                ) : (
                  (() => {
                    const issue = (issues ?? []).find((it) => it.issue_id === row.issueId);
                    const skin = issue ? (PHASE_SKIN[issue.phase] ?? PHASE_SKIN_FALLBACK) : PHASE_SKIN_FALLBACK;
                    return <span className={`size-[6px] flex-none rounded-full ${skin.dot}`} />;
                  })()
                )}
                <span className="min-w-0 flex-1 truncate">{row.label}</span>
                {row.kind === "issue" && (
                  <span className="flex-none text-[10.5px] text-tx3">{row.note}</span>
                )}
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-3 border-t border-line px-4 py-2 text-[10px] text-tx3">
          <span>↑↓ 选择</span>
          <span>Enter 打开</span>
          <span className="ml-auto font-mono">Esc 关闭</span>
        </div>
      </div>
    </div>
  );
}
