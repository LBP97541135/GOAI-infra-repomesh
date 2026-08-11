import { useState } from "react";
import type { IssueListItemView, IssueListResponse, Phase } from "../api/contract";
import { dayLabel, openedBy } from "../display";

/** issue 列表页：GitHub 式扁平列表 + Open/Closed 二元 + 行内徽标。
 *  设计定稿 DESIGN-DECISION-V2.md §1.4：流程感只存在于徽标，不存在于结构。
 *
 *  state / phase / phase_note 由读模型派生，本页只渲染（红线：前端不做状态映射）；
 *  下列配色表是展示皮肤，取自 Variant D 既有令牌。
 *
 *  两个筛选按钮（仓库 ▾ / 状态 ▾）已按裁决**撤除**：仓库维度列表响应里没有仓库字段，
 *  状态维度在分页之下做本地过滤等于拿部分结果冒充全量。服务端筛选进 backlog。
 *  保留的「待决策」是**当页过滤**，文案已标明，不装作全量。 */

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

function IssueRow({ item, onOpen }: { item: IssueListItemView; onOpen: (item: IssueListItemView) => void }) {
  const skin = PHASE_SKIN[item.phase] ?? { dot: "bg-tx2", badge: "border-line text-tx2" };

  const meta = [
    // issue_key 恒 null（无 Project 注册表）→ 显 issue_id 短版，不造 GitHub 式编号
    `#${item.issue_id.slice(0, 8)}`,
    `${openedBy(item)} 发起于 ${dayLabel(item.opened_at)}`,
    // 无 closed_at 这一持久化来源，closed 只能报最后更新，不能冒充关闭时间
    item.state === "closed" ? `最后更新 · ${dayLabel(item.updated_at)}` : `第 ${item.round_count} 轮交付`,
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
      {/* §2.1：paused 独立徽标，不改写 Open/Closed 归属；null ≠ active，故有值才渲染 */}
      {item.operational_status === "paused" && (
        <span className="mt-0.5 flex-none rounded-hard border border-salmon px-2 py-px text-[11px] text-salmon">
          已暂停
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
  tab,
  loading,
  loadingMore,
  error,
  sourceNote,
  onTab,
  onLoadMore,
  onRetry,
  onOpenIssue,
}: {
  data: IssueListResponse | null;
  tab: "open" | "closed";
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  /** 数据来源说明（live / replay），显式告知读者当前看的是什么 */
  sourceNote: string;
  onTab: (tab: "open" | "closed") => void;
  onLoadMore: () => void;
  onRetry: () => void;
  onOpenIssue: (item: IssueListItemView) => void;
}) {
  const [pendingOnly, setPendingOnly] = useState(false);

  const rows = (data?.issues ?? []).filter((i) => !pendingOnly || i.pending_decision_count > 0);

  const tabClass = (on: boolean) =>
    `flex items-center gap-1.5 border-b-2 px-0.5 py-2 text-[13px] ${
      on ? "border-amber text-tx" : "border-transparent text-tx2 hover:text-tx"
    }`;
  const countClass = (on: boolean) =>
    `rounded-hard bg-line px-1.5 font-mono text-[11px] ${on ? "text-amber" : "text-tx2"}`;

  return (
    <div className="max-w-[860px]">
      <div className="flex items-center gap-4 border-b border-line">
        <button className={tabClass(tab === "open")} onClick={() => onTab("open")}>
          Open
          {/* §2.5：计数不受 state 与分页影响，两个标签一次请求都点亮 */}
          <span className={countClass(tab === "open")}>{data ? data.open_count : "—"}</span>
        </button>
        <button className={tabClass(tab === "closed")} onClick={() => onTab("closed")}>
          Closed
          <span className={countClass(tab === "closed")}>{data ? data.closed_count : "—"}</span>
        </button>

        <button
          className={`ml-auto rounded-hard border px-2.5 py-[3px] text-[11.5px] ${
            pendingOnly ? "border-amber bg-amber/10 text-amber-hi" : "border-line text-tx2 hover:border-tx2"
          }`}
          onClick={() => setPendingOnly((v) => !v)}
          title="仅过滤已加载的条目，不代表全量结果"
        >
          待决策（仅当页）
        </button>
      </div>

      {error ? (
        <div className="mt-4 rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3">
          <div className="eyebrow mb-1 text-salmon">issue 列表加载失败</div>
          <p className="text-[12px] text-salmon">{error}</p>
          <button
            className="mt-2 rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
            onClick={onRetry}
          >
            重试
          </button>
        </div>
      ) : loading ? (
        <p className="px-1.5 py-8 text-center text-[12.5px] text-tx2">加载中…</p>
      ) : rows.length === 0 ? (
        <div className="px-1.5 py-8 text-center text-[12.5px] text-[#6b6046]">
          {pendingOnly ? "本页没有待决策的 issue" : tab === "open" ? "没有进行中的 issue" : "没有已完结的 issue"}
        </div>
      ) : (
        rows.map((item) => <IssueRow key={item.issue_id} item={item} onOpen={onOpenIssue} />)
      )}

      {!error && !loading && data?.next_cursor && !pendingOnly && (
        <button
          className="mt-3 rounded-hard border border-line px-3 py-1 text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi disabled:opacity-50"
          disabled={loadingMore}
          onClick={onLoadMore}
        >
          {loadingMore ? "加载中…" : "↓ 加载更多"}
        </button>
      )}

      <p className="pt-3 text-[11px] text-[#6b6046]">{sourceNote}</p>
    </div>
  );
}
