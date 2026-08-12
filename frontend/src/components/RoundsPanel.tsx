import type {
  GovernanceDecisionView,
  IssueDetailView,
  IssueRoundView,
  RollbackScopeView,
} from "../api/contract";
import type { Decision } from "../types";
import {
  PHASE_SKIN,
  PHASE_SKIN_FALLBACK,
  ROLLBACK_UNAVAILABLE_LABEL,
  dayLabel,
  eventTime,
  governanceLabel,
  governanceSkin,
  shortId,
} from "../display";

/** 轮次索引 + 跨轮决策查看（验收缺陷 B-6）。
 *
 *  v0.2 §3 的 `rounds` 一直在数据里，此前页面只在元数据行显示总数——历史轮次的
 *  决策因此没有查看入口。本面板按轮次列出，展开时取该轮的「待决策 + 已记录治理
 *  决策」（api/decisions.ts `fetchRoundDecisionHistory`，全部既有端点，无新映射）。
 *
 *  红线：phase 是读模型派生值，本页只渲染；下方配色是展示皮肤不是状态映射。
 *  待决策条目在这里是**只读信息**——批准动作只存在于上方决策夹（当前轮），历史轮
 *  没有授权单语义，放一个不能点的「批准」按钮比不放更糟。 */

// X2：八相皮肤用 display.ts 唯一表；治理决策徽标措辞与皮肤同源 display.ts

export interface RoundHistoryState {
  loading: boolean;
  error: string | null;
  pending: Decision[];
  recorded: GovernanceDecisionView[];
  /** E-1 交付卡的数据（§4.6）。null + rollbackError 说明为什么没有 */
  rollback: RollbackScopeView | null;
  rollbackError: string | null;
}

/** 归档按钮（B-4）的呈现规则：只对 `delivered` / `failed` 终态轮次给出动作；
 *  `archived` 显静态徽标；其余 phase 不显示按钮——后端对活跃轮次返回 409，
 *  给一个大概率失败的按钮不如不给（fail-closed 的界面版）。 */
const ARCHIVABLE_PHASES = new Set(["delivered", "failed"]);

export function RoundsPanel({
  detail,
  currentRoundId,
  expanded,
  history,
  archiveConfirmId,
  archivingId,
  onToggleRound,
  onArchiveRound,
  onRollbackRound,
}: {
  detail: IssueDetailView;
  /** 决策夹正在呈现的那一轮（active ?? latest）；展开该轮时提示去上方处理待决策 */
  currentRoundId: string | null;
  expanded: Record<string, boolean>;
  history: Record<string, RoundHistoryState>;
  /** 两步确认：第一次点击后该轮按钮变「确认归档？」，再点才真发请求 */
  archiveConfirmId: string | null;
  /** 归档请求在途的轮次 id；期间按钮禁用 */
  archivingId: string | null;
  onToggleRound: (round: IssueRoundView) => void;
  onArchiveRound: (round: IssueRoundView) => void;
  /** E-1：打开该轮的回滚对话框；范围表已随本轮展开取到，弹窗零额外取数 */
  onRollbackRound: (round: IssueRoundView, scope: RollbackScopeView) => void;
}) {
  if (detail.rounds.length === 0) return null;

  const repoName = (repositoryId: string) =>
    detail.repositories.find((r) => r.repository_id === repositoryId)?.name ?? shortId(repositoryId);

  return (
    <>
      <div className="microlabel pt-5 pb-2">
        轮次
        <span className="pl-2 text-[10px] tracking-normal text-tx3">
          决策是轮次粒度（round_id = 交付 id），展开查看该轮全部决策
        </span>
      </div>
      <div className="rounded-hard border border-line">
        {detail.rounds.map((round, i) => {
          const open = expanded[round.round_id] ?? false;
          const state = history[round.round_id];
          const isCurrent = round.round_id === currentRoundId;
          // 局部常量而非 state.rollback：回调闭包里 TS 的收窄跟不到属性访问，
          // 否则只能靠一个 `!` 断言把「取到了没有」这件事糊过去
          const rollbackScope = state?.rollback ?? null;
          // A-4：这两个字段与 created_at 同源（该轮 PlanSnapshot），为 null 就是
          // 「轮次行落库了、该轮快照没落库」。**只陈述这一件事，不判断为什么**——
          // 实测（8100，两个活标本 5c1b3567 / 35e66beb）「刚物化尚未开工」与
          // 「物化半途中断」两条来路的读模型投影逐字段一致，界面无从分辨，
          // 挑一句说就是拿信号冒充原因。措辞与皮肤都取中性，理由写在 title 里。
          const missingPlanVersion = round.plan_version === null;
          const missingUpdatedAt = round.updated_at === null;
          const noSnapshot = missingPlanVersion || missingUpdatedAt;
          return (
            <div key={round.round_id} className="border-b border-panel last:border-b-0">
              {/* 行内两个动作（展开 / 归档）不能嵌套 button，行是布局容器 */}
              <div className="flex items-baseline gap-2.5 px-3 py-2 hover:bg-[#241e13]">
                <button
                  className="flex min-w-0 flex-1 items-baseline gap-2.5 text-left"
                  onClick={() => onToggleRound(round)}
                >
                  <span className="font-mono text-[11.5px] text-tx">第 {i + 1} 轮</span>
                  <span
                    className={`rounded-hard border px-1.5 font-mono text-[10px] ${PHASE_SKIN[round.phase]?.badge ?? PHASE_SKIN_FALLBACK.badge}`}
                  >
                    {round.phase}
                  </span>
                  <span className="text-[10.5px] text-tx3">
                    计划 {missingPlanVersion ? "版本未落库" : `v${round.plan_version}`} · 更新于{" "}
                    {missingUpdatedAt ? "时间未落库" : dayLabel(round.updated_at)}
                    {isCurrent ? " · 决策夹当前轮" : ""}
                  </span>
                  {noSnapshot && (
                    <span
                      className="flex-none rounded-hard border border-line px-1.5 font-mono text-[10px] text-tx2"
                      title={
                        "本轮的 PlanSnapshot 尚未落库：计划版本、创建时间、更新时间三个字段同源，此刻一起为空。\n" +
                        "两条真实来路读模型都投影成这一个形状——① 刚物化、执行还没产出任何东西；② 物化半途中断。" +
                        "服务端没有给出区分二者的字段（两个活标本逐字段一致），所以这里不替它挑一句。\n" +
                        "轮次行本身是真的：展开仍可查看该轮已记录的决策与交付卡。"
                      }
                    >
                      本轮尚无快照
                    </span>
                  )}
                </button>
                {round.phase === "archived" && (
                  <span className="flex-none rounded-hard border border-line px-1.5 font-mono text-[10px] text-tx2">
                    已归档
                  </span>
                )}
                {ARCHIVABLE_PHASES.has(round.phase) && (
                  <button
                    className={`flex-none rounded-hard border px-1.5 font-mono text-[10px] ${
                      archiveConfirmId === round.round_id
                        ? "border-salmon text-salmon"
                        : "border-line text-tx2 hover:border-amber hover:text-amber-hi"
                    }`}
                    disabled={archivingId === round.round_id}
                    onClick={() => onArchiveRound(round)}
                  >
                    {archivingId === round.round_id
                      ? "归档中…"
                      : archiveConfirmId === round.round_id
                        ? "确认归档？"
                        : "归档本轮"}
                  </button>
                )}
                <button className="flex-none font-mono text-[10.5px] text-tx2" onClick={() => onToggleRound(round)}>
                  {open ? "▾ 收起" : "决策 ›"}
                </button>
              </div>

              {open && (
                <div className="border-t border-panel bg-[#161209] px-3 py-2.5">
                  {state?.loading && <p className="text-[11.5px] text-tx2">决策取用中…</p>}
                  {state?.error && <p className="text-[11.5px] text-salmon">决策取用失败：{state.error}</p>}
                  {state && !state.loading && !state.error && (
                    <>
                      {/* 交付卡（E-1 挂载点）。回滚是整 change set 的动作，所以它
                          挂在轮次上而不是某一条决策上；入口仅在服务端报告
                          available 时出现（§4.6），不给一个注定 409/无事可做的按钮。 */}
                      <div className="mb-2 flex flex-wrap items-baseline gap-x-2.5 gap-y-1 rounded-hard border border-line bg-panel-2 px-2.5 py-2">
                        <span className="font-mono text-[10px] tracking-[0.1em] text-tx3">交付</span>
                        {rollbackScope === null ? (
                          <span className="text-[11px] text-tx3">
                            回滚范围未取到{state.rollbackError ? `：${state.rollbackError}` : ""}
                          </span>
                        ) : rollbackScope.available ? (
                          <>
                            <span className="text-[11px] text-tx2">
                              {rollbackScope.repositories.filter((r) => r.action !== "none").length}{" "}
                              个仓库有已发布候选可撤销
                              {rollbackScope.recovery_in_progress ? " · 已有恢复计划在执行" : ""}
                            </span>
                            <button
                              className="ml-auto flex-none rounded-hard border border-line px-1.5 font-mono text-[10px] text-tx2 hover:border-amber hover:text-amber-hi"
                              onClick={() => onRollbackRound(round, rollbackScope)}
                            >
                              回滚…
                            </button>
                          </>
                        ) : (
                          <span className="text-[11px] text-tx3">
                            {rollbackScope.unavailable_reason
                              ? ROLLBACK_UNAVAILABLE_LABEL[rollbackScope.unavailable_reason]
                              : "服务端报告本轮无可回滚项，但没有给出原因。"}
                          </span>
                        )}
                      </div>

                      {state.pending.length === 0 && state.recorded.length === 0 && (
                        <p className="text-[11.5px] text-tx3">该轮无待决策事项，也无已记录的治理决策。</p>
                      )}

                      {state.pending.length > 0 && (
                        <div className="pb-1.5">
                          <div className="pb-1 font-mono text-[10px] tracking-[0.1em] text-tx3">待决策</div>
                          {state.pending.map((d) => (
                            <div key={d.id} className="flex items-baseline gap-2 py-0.5">
                              <span className="rounded-hard border border-amber px-1.5 font-mono text-[10px] text-amber">
                                {d.kind === "approve" ? "放行" : "关注"}
                              </span>
                              <span className="min-w-0 flex-1 truncate text-[11.5px] text-kraft">{d.title}</span>
                              {isCurrent && <span className="flex-none text-[10.5px] text-tx3">在上方决策夹处理</span>}
                            </div>
                          ))}
                        </div>
                      )}

                      {state.recorded.length > 0 && (
                        <div>
                          <div className="pb-1 font-mono text-[10px] tracking-[0.1em] text-tx3">已记录治理决策</div>
                          {state.recorded.map((g) => (
                            <div key={g.id} className="flex flex-wrap items-baseline gap-x-2 py-0.5">
                              <span
                                className={`rounded-hard border px-1.5 font-mono text-[10px] ${governanceSkin(g.decision)}`}
                              >
                                {governanceLabel(g.decision)}
                              </span>
                              <span className="font-mono text-[11px] text-tx">{repoName(g.repository_id)}</span>
                              <span className="font-mono text-[10.5px] text-tx2">head {g.head_sha.slice(0, 12)}</span>
                              {/* decided_by 只有 agent id，花名册解析不进这里——短版如实，不冒充人名 */}
                              <span className="text-[10.5px] text-tx3">
                                AGENT {shortId(g.decided_by_agent_id)} · {eventTime(g.decided_at)}
                              </span>
                              <span className="w-full pl-0 text-[11px] text-tx2">{g.reason}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
