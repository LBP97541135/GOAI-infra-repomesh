import { useState } from "react";
import type { Phase } from "../api/contract";
import type { IssueListItem, IssueListResponse } from "../data/issues";

/** issue 列表页（CONS-41）：GitHub 式扁平列表 + Open/Closed 二元 + 行内徽标。
 *  设计定稿 DESIGN-DECISION-V2.md §1.4：流程感只存在于徽标，不存在于结构。
 *  state/phase 由读模型派生，本页只渲染（红线：前端不做状态映射）；
 *  下列配色表是展示皮肤，取自 Variant D 既有令牌。 */

const PHASE_SKIN: Record<Phase, { dot: string; badge: string }> = {
  contract: { dot: "bg-tx2", badge: "border-line text-tx2" },
  plan: { dot: "bg-tx2", badge: "border-line text-tx2" },
  execute: { dot: "bg-bluegray", badge: "border-bluegray text-bluegray" },
  validate: { dot: "bg-salmon", badge: "border-salmon text-salmon" },
  release: { dot: "bg-amber", badge: "border-line text-tx2" },
  delivered: { dot: "bg-olive", badge: "border-olive text-olive" },
  failed: { dot: "bg-salmon", badge: "border-salmon text-salmon" },
  archived: { dot: "bg-tx2", badge: "border-line text-tx2" },
};

function IssueRow({ item, onOpen }: { item: IssueListItem; onOpen: (item: IssueListItem) => void }) {
  const skin = PHASE_SKIN[item.phase];
  const meta = [
    `#${item.number}`,
    `${item.author_name ?? "发起人未关联"} 发起于 ${item.created_label}`,
    item.state === "closed" && item.closed_label ? `已交付 · ${item.closed_label}` : `第 ${item.delivery_rounds} 轮交付`,
    `${item.repository_count} 仓`,
  ].join(" · ");

  return (
    <button
      className="flex w-full items-start gap-2.5 border-b border-panel px-1.5 py-3 text-left hover:bg-[#1a160e]"
      onClick={() => onOpen(item)}
    >
      <span className={`mt-[7px] size-2 flex-none rounded-full ${skin.dot}`} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13.5px] text-tx">{item.title}</span>
        <span className="mt-[3px] block truncate text-[11.5px] text-tx2">{meta}</span>
      </span>
      {item.pending_decision_count > 0 && (
        <span className="mt-0.5 flex-none rounded-hard bg-amber px-2 py-px text-[11px] font-semibold text-[#191308]">
          {item.pending_decision_count} 项待决策
        </span>
      )}
      <span className={`mt-0.5 flex-none rounded-hard border px-2 py-px text-[11px] ${skin.badge}`}>
        {item.phase_note}
      </span>
    </button>
  );
}

export function IssueListPage({
  data,
  onOpenIssue,
  onToast,
}: {
  data: IssueListResponse;
  onOpenIssue: (item: IssueListItem) => void;
  onToast: (text: string) => void;
}) {
  const [tab, setTab] = useState<"open" | "closed">("open");
  const [pendingOnly, setPendingOnly] = useState(false);

  const rows = data.items
    .filter((i) => i.state === tab)
    .filter((i) => !pendingOnly || i.pending_decision_count > 0);

  const tabClass = (on: boolean) =>
    `flex items-center gap-1.5 border-b-2 px-0.5 py-2 text-[13px] ${
      on ? "border-amber text-tx" : "border-transparent text-tx2 hover:text-tx"
    }`;

  return (
    <div className="max-w-[860px]">
      <div className="flex items-center gap-4 border-b border-line">
        <button className={tabClass(tab === "open")} onClick={() => setTab("open")}>
          Open
          <span className={`rounded-hard bg-line px-1.5 font-mono text-[11px] ${tab === "open" ? "text-amber" : "text-tx2"}`}>
            {data.open_count}
          </span>
        </button>
        <button className={tabClass(tab === "closed")} onClick={() => setTab("closed")}>
          Closed
          <span className={`rounded-hard bg-line px-1.5 font-mono text-[11px] ${tab === "closed" ? "text-amber" : "text-tx2"}`}>
            {data.closed_count}
          </span>
        </button>

        <div className="ml-auto flex gap-2">
          <button
            className="rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-tx2"
            onClick={() => onToast("按仓库筛选待 CONS-32 仓库读模型接入")}
          >
            仓库 ▾
          </button>
          <button
            className="rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-tx2"
            onClick={() => onToast("按 phase 筛选待 CONS-31 issue 读模型接入")}
          >
            状态 ▾
          </button>
          <button
            className={`rounded-hard border px-2.5 py-[3px] text-[11.5px] ${
              pendingOnly ? "border-amber bg-amber/10 text-amber-hi" : "border-line text-tx2 hover:border-tx2"
            }`}
            onClick={() => setPendingOnly((v) => !v)}
          >
            待决策
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="px-1.5 py-8 text-center text-[12.5px] text-[#6b6046]">
          {pendingOnly ? "没有待决策的 issue" : tab === "open" ? "没有进行中的 issue" : "没有已完结的 issue"}
        </div>
      ) : (
        rows.map((item) => <IssueRow key={item.issue_id} item={item} onOpen={onOpenIssue} />)
      )}

      {tab === "closed" && data.closed_truncated > 0 && !pendingOnly && (
        <div className="px-1.5 py-3 text-[11.5px] text-[#6b6046]">
          + {data.closed_truncated} 个已完结 issue（分页待 CONS-31 接入）
        </div>
      )}
    </div>
  );
}
