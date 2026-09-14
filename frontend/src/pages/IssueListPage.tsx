import { useState } from "react";
import { ChevronDown, Inbox } from "lucide-react";
import type { IssueListItemView, IssueListResponse } from "../api/contract";
import { PHASE_SKIN, PHASE_SKIN_FALLBACK, dayLabel, shortId } from "../display";
import { Modal } from "../components/Modal";
import { ErrorPanel, LoadingLine } from "../components/StatusBlocks";

/** issue 列表页：GitHub 式扁平列表 + Open/Closed 二元 + 行内徽标。
 *  设计定稿 DESIGN-DECISION-V2.md §1.4：流程感只存在于徽标，不存在于结构。
 *
 *  state / phase / phase_note / archived 由读模型派生，本页只渲染（红线：前端不做
 *  状态映射）；下列配色表是展示皮肤，取自 Variant D 既有令牌。
 *
 *  两个筛选按钮（仓库 ▾ / 状态 ▾）已按裁决**撤除**：仓库维度列表响应里没有仓库字段，
 *  状态维度在分页之下做本地过滤等于拿部分结果冒充全量。服务端筛选进 backlog。
 *  保留的「待决策」是**当页过滤**，文案已标明，不装作全量。「已归档」开关是
 *  服务端过滤（契约 v0.5 §2 `include_archived`），默认视图与两个计数都不含墓碑。 */

function IssueRow({
  item,
  onOpen,
  canArchive,
  onArchive,
  onPurge,
}: {
  item: IssueListItemView;
  onOpen: (item: IssueListItemView) => void;
  /** replay 夹具不可篡改（v0.5 §3），回放模式下行内不出现归档/清除入口 */
  canArchive: boolean;
  onArchive: (item: IssueListItemView) => void;
  onPurge: (item: IssueListItemView) => void;
}) {
  // X2：八相皮肤唯一表在 display.ts（release 从灰改琥珀=主脑裁决，已记交付说明）
  const skin = PHASE_SKIN[item.phase] ?? PHASE_SKIN_FALLBACK;

  // 2026-09-08 用户裁决：列表降噪——标题是唯一锚点，元信息只留「编号 · 时间」
  // 一行等宽弱文本（轮次/仓数/发起人退出列表，详情页都有）。
  const meta = `#${shortId(item.issue_id)} · 更新于 ${dayLabel(item.updated_at)}`;

  return (
    // 行主体是按钮，归档是兄弟按钮——button 不能嵌套 button，拆开才合法
    <div className="flex w-full items-start border-b border-panel">
      <button
        className="flex min-w-0 flex-1 items-start gap-2.5 px-1.5 py-2.5 text-left hover:bg-well"
        onClick={() => onOpen(item)}
        title={item.title}
      >
        <span className={`mt-[7px] size-2 flex-none rounded-full ${skin.dot}`} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13.5px] font-medium text-tx">{item.title}</span>
          <span className="mt-[2px] block truncate font-mono text-[11px] text-tx3">{meta}</span>
        </span>

        {item.pending_decision_count > 0 && (
          <span className="mt-0.5 flex-none rounded-hard bg-amber px-2 py-px text-[11px] font-semibold text-on-amber">
            {item.pending_decision_count} 项待决策
          </span>
        )}
        {/* §2.3：需求已落库、尚未物化成执行计划——Org Leader 的下一个动作是驱动
            发现链并物化。与 phase_note「计划 vN 待物化」同一分支派生，行内徽标让
            它在列表里一眼可扫。 */}
        {item.pending_planning && (
          <span className="mt-0.5 flex-none rounded-hard border border-amber px-2 py-px text-[11px] text-amber">
            待处理
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

      {/* v0.5：墓碑行只显示徽标不再提供归档入口；归档不改写 state/phase。
          2026-09-08：已归档行追加「彻底清除」入口（归档之外的第二个不可逆动作，
          走独立确认弹窗）——live 模式才出现。 */}
      {item.archived ? (
        <>
          <span
            className="mt-3 mr-1.5 flex-none rounded-hard border border-line px-2 py-px text-[11px] text-tx2"
            title={item.archived_at ? `归档于 ${dayLabel(item.archived_at)}` : undefined}
          >
            已归档
          </span>
          {canArchive && (
            <button
              className="mt-3 mr-1.5 flex-none rounded-hard border border-transparent px-2 py-px text-[11px] text-tx3 hover:border-salmon hover:text-salmon"
              title="硬删除快照、决策链与审计记录（不可逆，仅保留一条清除审计）"
              onClick={() => onPurge(item)}
            >
              彻底清除
            </button>
          )}
        </>
      ) : canArchive ? (
        <button
          className="mt-3 mr-1.5 flex-none rounded-hard border border-transparent px-2 py-px text-[11px] text-tx3 hover:border-salmon hover:text-salmon"
          title="移出默认列表（数据全部保留，可按 issue id 直达详情）"
          onClick={() => onArchive(item)}
        >
          归档
        </button>
      ) : null}
    </div>
  );
}

export function IssueListPage({
  data,
  tab,
  loading,
  loadingMore,
  error,
  sourceNote,
  showArchived,
  canArchive,
  onTab,
  onToggleArchived,
  onArchive,
  onPurge,
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
  /** v0.5：服务端 include_archived 过滤的开关状态 */
  showArchived: boolean;
  canArchive: boolean;
  onTab: (tab: "open" | "closed") => void;
  onToggleArchived: () => void;
  /** 用户在确认弹窗里点了「确认归档」之后回调（replay 模式不触发） */
  onArchive: (item: IssueListItemView) => void;
  /** 用户在确认弹窗里点了「确认彻底清除」之后回调（replay 模式不触发） */
  onPurge: (item: IssueListItemView) => void;
  onLoadMore: () => void;
  onRetry: () => void;
  onOpenIssue: (item: IssueListItemView) => void;
}) {
  const [pendingOnly, setPendingOnly] = useState(false);
  const [pendingArchive, setPendingArchive] = useState<IssueListItemView | null>(null);
  const [pendingPurge, setPendingPurge] = useState<IssueListItemView | null>(null);

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
          {/* §2.5：计数不受 state 与分页影响，两个标签一次请求都点亮；v0.5 起两个
              计数与默认列表同一口径，都不含已归档 */}
          <span className={countClass(tab === "open")}>{data ? data.open_count : "—"}</span>
        </button>
        <button className={tabClass(tab === "closed")} onClick={() => onTab("closed")}>
          Closed
          <span className={countClass(tab === "closed")}>{data ? data.closed_count : "—"}</span>
        </button>

        <button
          className={`ml-auto rounded-hard border px-2.5 py-[3px] text-[11.5px] ${
            showArchived ? "border-amber bg-amber/10 text-amber-hi" : "border-line text-tx2 hover:border-tx2"
          }`}
          onClick={onToggleArchived}
          title="已归档的 issue 移出了默认列表与计数（数据全部保留）"
        >
          已归档
        </button>
        <button
          className={`rounded-hard border px-2.5 py-[3px] text-[11.5px] ${
            pendingOnly ? "border-amber bg-amber/10 text-amber-hi" : "border-line text-tx2 hover:border-tx2"
          }`}
          onClick={() => setPendingOnly((v) => !v)}
          title="仅过滤已加载的条目，不代表全量结果"
        >
          待决策（仅当页）
        </button>
      </div>

      {error ? (
        <ErrorPanel title="issue 列表加载失败" message={error} onRetry={onRetry} />
      ) : loading ? (
        <LoadingLine className="px-1.5" />
      ) : rows.length === 0 ? (
        <div className="px-1.5 py-10 text-center text-[12.5px] text-tx3">
          <Inbox size={22} className="mx-auto mb-2 text-tx3/70" />
          {showArchived
            ? "没有已归档的 issue"
            : pendingOnly
              ? "本页没有待决策的 issue"
              : tab === "open"
                ? "没有进行中的 issue"
                : "没有已完结的 issue"}
        </div>
      ) : (
        rows.map((item) => (
          <IssueRow
            key={item.issue_id}
            item={item}
            onOpen={onOpenIssue}
            canArchive={canArchive}
            onArchive={setPendingArchive}
            onPurge={setPendingPurge}
          />
        ))
      )}

      {!error && !loading && data?.next_cursor && !pendingOnly && (
        <button
          className="mt-3 inline-flex items-center gap-1.5 rounded-hard border border-line px-3 py-1 text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi disabled:opacity-50"
          disabled={loadingMore}
          onClick={onLoadMore}
        >
          <ChevronDown size={13} />
          {loadingMore ? "加载中…" : "加载更多"}
        </button>
      )}

      <p className="pt-3 text-[11px] text-tx3">{sourceNote}</p>

      {/* 归档确认（v0.5 §3）：写墓碑前把语义说清——不是删除，决策与审计全保留 */}
      <Modal
        open={pendingArchive !== null}
        onClose={() => setPendingArchive(null)}
        className="m-auto w-[min(460px,92vw)] rounded-hard border border-line-strong bg-panel p-0 text-tx shadow-pop"
      >
        {pendingArchive && (
          <div className="p-5">
            <div className="eyebrow mb-1 text-amber">归档 issue</div>
            <p className="text-[14px] font-semibold">{pendingArchive.title}</p>
            <p className="mt-2 text-[12px] leading-relaxed text-tx2">
              归档只是把它移出默认列表和 Open/Closed 计数——快照、决策链、checkpoint
              决策与审计记录全部保留，按 issue id 直达详情仍可访问。这不是删除。
            </p>
            <p className="mt-1.5 text-[11.5px] text-tx3">
              仍有进行中轮次的 issue 无法归档（后端会拒绝并说明原因）。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                className="rounded-hard border border-line px-3 py-1.5 text-[12px] text-tx2 hover:border-tx2 hover:text-tx"
                onClick={() => setPendingArchive(null)}
              >
                取消
              </button>
              <button
                className="rounded-hard bg-amber px-3 py-1.5 text-[12px] font-semibold text-on-amber hover:brightness-110"
                onClick={() => {
                  onArchive(pendingArchive);
                  setPendingArchive(null);
                }}
              >
                确认归档
              </button>
            </div>
          </div>
        )}
      </Modal>

      {/* 彻底清除确认（2026-09-08 用户裁决）：与归档分开的第二动作——不可逆，
          弹窗必须把「删什么、留什么、不可恢复」三件事说全才配让人按下去 */}
      <Modal
        open={pendingPurge !== null}
        onClose={() => setPendingPurge(null)}
        className="m-auto w-[min(480px,92vw)] rounded-hard border border-salmon/60 bg-panel p-0 text-tx shadow-pop"
      >
        {pendingPurge && (
          <div className="p-5">
            <div className="eyebrow mb-1 text-salmon">彻底清除 issue</div>
            <p className="text-[14px] font-semibold">{pendingPurge.title}</p>
            <p className="mt-2 text-[12px] leading-relaxed text-tx2">
              将<b className="text-salmon">硬删除</b>计划快照、决策链、checkpoint
              决策与审计记录——<b className="text-salmon">不可恢复</b>。仅保留一条
              「已清除」审计（谁、何时），删除动作本身可追溯。
            </p>
            <p className="mt-1.5 text-[11.5px] text-tx3">
              只对已归档的 issue 开放；若它尚未归档，后端会拒绝并说明原因。
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                className="rounded-hard border border-line px-3 py-1.5 text-[12px] text-tx2 hover:border-tx2 hover:text-tx"
                onClick={() => setPendingPurge(null)}
              >
                取消
              </button>
              <button
                className="rounded-hard bg-salmon px-3 py-1.5 text-[12px] font-semibold text-on-amber hover:brightness-110"
                onClick={() => {
                  onPurge(pendingPurge);
                  setPendingPurge(null);
                }}
              >
                确认彻底清除
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
