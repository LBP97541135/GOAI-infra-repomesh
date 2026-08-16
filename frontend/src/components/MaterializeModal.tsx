import { Modal } from "./Modal";
import type { ApprovalPrincipal } from "./DiscoveryApproval";
import { PolicyDigest, type PolicyDraftState } from "./SupervisionPolicyCard";

/** 「物化并开工」确认弹窗（批次 C-3，设计定稿 `full-loop-gui-design-20260812.md` ③）。
 *
 *  设计定稿把这一步定为**整条链的第二个不可逆感知点**（第一个是 merge 审批），
 *  所以弹窗的正文不是一句「确定吗」，而是**把即将发生的事数出来**：N 个任务、
 *  M 个团队、每队两间房。用户在按下去之前应当知道自己在创建多少东西。
 *
 *  N / M 的来路各不相同，弹窗里分别标注，取不到就说取不到：
 *   - N = 发现读投影 `integration.task_dag_count`（服务端计数，原样透出）；
 *   - M = 计划纸面 `execution_batches` 去重后的仓库数（「每仓一队」）。计划纸面
 *     还没取到时为 null——**不拿节点数或候选数顶替**，那是另一个数。
 *
 *  弹窗壳沿用 aefd39a 的统一 Modal（Esc + 焦点陷阱 + 关闭即卸载），配色全部取
 *  index.css 既有令牌，不新增颜色语义。 */
export function MaterializeModal({
  open,
  planVersion,
  taskCount,
  teamCount,
  unresolvedCount,
  policy,
  principal,
  submitting,
  errorText,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  /** 承载计划的草稿快照版本（发现读投影 `plan_version`） */
  planVersion: number;
  /** N：`integration.task_dag_count` */
  taskCount: number;
  /** M：计划里的仓库数；计划纸面未就绪时 null */
  teamCount: number | null;
  /** 计划里 catalog 查无仓库的节点数（>0 时 M 与实际建队数可能不等） */
  unresolvedCount: number;
  /** 监管策略草稿（迁移 5-1b · F5，设计文档 §3.3）。**这一行的理由不是「顺便也显示
   *  一下」**：按下这个按钮之后档案就锁死了（全仓没有更新拓扑的端点），所以最后一次
   *  能看到自己选了什么的机会必须在按钮旁边。 */
  policy: PolicyDraftState;
  principal: ApprovalPrincipal;
  submitting: boolean;
  /** 服务端 detail 原文（409 等）。**不翻译不软化**，原样贴出来 */
  errorText: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const blocked = principal.state !== "ready";

  const facts: Array<[string, string]> = [
    ["计划快照", `v${planVersion}`],
    ["将创建任务", `${taskCount} 个`],
    ["将组建团队", teamCount === null ? "取不到（见下）" : `${teamCount} 个`],
    ["执行主体", principal.label],
  ];

  return (
    <Modal
      open={open}
      onClose={onCancel}
      className="m-auto w-[min(520px,92vw)] rounded-[3px] border border-[#4a4128] bg-panel p-0 text-tx shadow-[0_24px_70px_rgba(0,0,0,0.7)]"
    >
      <div className="flex items-start justify-between border-b border-line px-[22px] pt-5 pb-3.5">
        <div>
          <span className="eyebrow">PLAN MATERIALIZATION</span>
          <h2 className="mt-1 text-[16px] font-semibold text-cream">物化并开工</h2>
        </div>
        <button className="text-[18px] text-tx2" aria-label="关闭" onClick={onCancel}>
          ×
        </button>
      </div>

      <div className="px-[22px] py-4">
        <div className="mb-3.5 grid grid-cols-2 gap-2">
          {facts.map(([k, v]) => (
            <div key={k} className="rounded-hard border border-line bg-panel-2 px-[11px] py-2">
              <span className="block font-mono text-[9.5px] tracking-[0.1em] text-tx2">{k}</span>
              <b className="text-[12px] text-cream">{v}</b>
            </div>
          ))}
        </div>

        <p className="text-[12.5px] leading-[1.75] text-tx">
          将按计划快照 <b className="text-cream">v{planVersion}</b> 创建{" "}
          <b className="text-cream">{taskCount} 个任务</b>、组建{" "}
          <b className="text-cream">{teamCount === null ? "若干" : `${teamCount} 个`}团队</b>
          （每仓一队，各带 teamRoom 与 leaderDM 双房间）。任务按计划的执行批次自行转起。
        </p>

        {/* ── 监管策略摘要（§3.3）───────────────────────────────────────────
            物化会顺手建出项目档案（`EnsureProjectAgentTopology`），档案的三个策略
            字段就取自这份草稿；建完之后全仓**没有任何更新拓扑的端点**，所以这是
            用户最后一次能看见自己选了什么的时刻。取不到时如实说取不到，不拿
            「大概是全自动」顶替——猜错的那一半会把「设过了」说成「没设过」。 */}
        <div className="mt-3 rounded-hard border border-line bg-panel-2 px-3 py-2.5">
          <span className="block font-mono text-[9.5px] tracking-[0.1em] text-tx2">监管策略</span>
          <div className="mt-1">
            {policy.kind === "set" ? (
              <PolicyDigest draft={policy.draft} />
            ) : policy.kind === "unset" ? (
              // 与卡片同一句话，同一个语气：这是陈述不是警告，全自动是默认值不是故障
              <p className="text-[12px] leading-[1.75] text-tx">
                <b className="text-cream">未设定</b>——本次将以全自动运行，没有任何人工卡点，
                这个需求不会产生任何审核待办。
                <span className="block text-[11px] text-tx3">
                  要改还来得及：取消本弹窗，用上方「监管策略」卡片的「配置」。
                </span>
              </p>
            ) : policy.kind === "sealed" ? (
              <p className="text-[12px] leading-[1.7] text-tx3">
                本需求已有项目档案，本次不再读草稿——策略以下方「监管策略」段显示的那份为准。
              </p>
            ) : policy.kind === "loading" ? (
              <p className="text-[12px] text-tx2">读取中…</p>
            ) : (
              <p className="text-[12px] leading-[1.7] text-tx3">
                这一次没取到策略草稿（
                {policy.kind === "unauthenticated" || policy.kind === "forbidden"
                  ? policy.detail
                  : policy.kind === "error"
                    ? policy.message
                    : "拓扑状态未落定"}
                ）。<b className="text-tx2">界面不猜</b>：物化仍会按服务端那边真实存在的草稿建档案，
                而这里说不出那是哪一份。
              </p>
            )}
          </div>
        </div>

        {/* 不可逆提示：设计定稿把这一步与 merge 审批并列为两个不可逆感知点。
            措辞不许诺「可以撤销」——界面没有反向按钮，退出动线是另一件事。 */}
        <div className="mt-3 border-l-2 border-amber bg-panel-2 px-3 py-2 text-[12px] leading-[1.7] text-kraft">
          <b className="mr-1.5 font-mono tracking-[0.08em] text-amber">不可逆</b>
          这是整条链的第二个不可逆感知点（第一个是 merge 审批）。任务与团队一经创建即由执行面接管，
          界面<b className="text-cream">没有「撤销物化」这一步</b>——退出动线是整 change set 回滚，或取消 issue。
        </div>

        {teamCount === null && (
          <p className="mt-2.5 text-[11.5px] leading-[1.7] text-tx3">
            团队数取自计划纸面的执行批次，而计划纸面此刻没取到（见下方「计划 DAG」区块）。
            这里不拿别的数顶替——真实数目以服务端回执的 <span className="font-mono">team_count</span> 为准。
          </p>
        )}

        {unresolvedCount > 0 && (
          <p className="mt-2.5 text-[11.5px] leading-[1.7] text-salmon">
            计划里有 {unresolvedCount} 个名字在 catalog 中查无仓库（DAG 面板按虚线节点留痕）。
            要不要为它们建队由服务端决定，界面不猜——上面的团队数是计划里的仓库数，
            可能与回执的 <span className="font-mono">team_count</span> 不等。
          </p>
        )}

        {principal.state === "replay" && (
          <div className="mt-3 border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] leading-[1.7] text-[#e8a184]">
            <b className="mr-1.5 font-mono tracking-[0.08em]">回放模式</b>
            物化会在真实世界里建任务、建团队、开房间，回放里没有可写的对象。提交已禁用——
            就地伪造一份「已物化」等于对着夹具演一遍不可逆动作，刷新即消失。加 ?source=live 后可真实提交。
          </div>
        )}

        {principal.state === "missing" && (
          <div className="mt-3 border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] leading-[1.7] text-[#e8a184]">
            <b className="mr-1.5 font-mono tracking-[0.08em]">执行主体未接入</b>
            花名册里没有该组织可用的 organization_leader，也没有配置 VITE_GOVERNANCE_AGENT_ID 覆盖。
            提交已禁用——物化必须记在一个真实主体名下。
          </div>
        )}

        {errorText && (
          // 服务端 detail 原文。409 的原因不止一种（受控项目的 REPOSITORY_SCOPE
          // 检查点未过、计划尚未生成…），归并成一句「物化失败」会把可自助解决的
          // 前置问题伪装成系统故障。
          <div className="mt-3 border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] leading-[1.7] break-words text-[#e8a184]">
            <b className="mr-1.5 font-mono tracking-[0.08em]">服务端拒绝</b>
            {errorText}
          </div>
        )}
      </div>

      <div className="flex justify-end gap-2.5 px-[22px] pt-3.5 pb-[18px]">
        <button
          className="rounded-hard border border-line bg-transparent px-3.5 py-2 text-[12.5px] text-tx"
          onClick={onCancel}
        >
          取消
        </button>
        <button
          className="rounded-hard bg-amber px-4 py-2 text-[12.5px] font-extrabold text-[#191308] hover:bg-amber-hi disabled:cursor-not-allowed disabled:opacity-40"
          disabled={submitting || blocked}
          onClick={onConfirm}
        >
          {submitting ? "物化中…" : principal.state === "resolving" ? "解析主体…" : "确认物化并开工"}
        </button>
      </div>
    </Modal>
  );
}
