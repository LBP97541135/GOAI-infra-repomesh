import { useEffect, useState } from "react";
import type { RollbackScopeView } from "../api/contract";
import type { ApprovalPrincipal } from "./ApprovalModal";
import { Modal } from "./Modal";
import {
  ROLLBACK_ACTION_LABEL,
  ROLLBACK_ACTION_SKIN,
  ROLLBACK_STATE_LABEL,
  ROLLBACK_STATE_SKIN,
  ROLLBACK_UNAVAILABLE_LABEL,
} from "../display";

/** 回滚对话框（批次 E-1，设计定稿 `full-loop-gui-design-20260812.md` ④）。
 *
 *  **主体是范围表，不是确认文案**：每仓一行——仓名 / 当前态（未 merge、已 merge）/
 *  对应动作（withhold 免费撤回 / revert PR 逆序第 k 步）。三阶段回滚设计翻译成
 *  用户语言就是这张表；一句「确定要回滚吗」什么也没告诉人。
 *
 *  表里每一格都是服务端给的（§4.6 读投影）：`state` 由读模型按 merge_sha 判、
 *  `step` 是 recovery planner 预览出的动作序号。前端不按 merge_order 自己数第几步。
 *
 *  按钮语义如实：提交的是 `ROLLBACK_REQUIRED` 治理决策（先堵死 merge gate），
 *  执行由恢复 saga 在决策记录后接管。**不许诺「一键干净还原」**——琥珀条如实
 *  交代 revert PR 仍要过 CI 与门禁、冲突会自动开处理任务。
 *
 *  粒度＝GUI 裁决 4：整 change set 一体回滚，**无单仓选择框**。表是只读的。 */
export function RollbackModal({
  open,
  roundLabel,
  scope,
  principal,
  submitting,
  errorText,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  /** 「第 N 轮」，与决策夹同一份算法 */
  roundLabel: string;
  /** null = 范围还没取到；组件自己说「取用中」，不拿空表冒充「无可回滚项」 */
  scope: RollbackScopeView | null;
  principal: ApprovalPrincipal;
  submitting: boolean;
  /** 服务端 detail 原文（409 等）。不翻译不软化 */
  errorText: string | null;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);

  // 每次打开都重置：确认与理由都不该从上一次遗留下来
  useEffect(() => {
    if (open) {
      setConfirmed(false);
      setReason("");
    }
  }, [open]);

  const rows = scope?.repositories ?? [];
  const actionable = rows.filter((row) => row.action !== "none");
  const blocked =
    principal.state !== "ready" || scope === null || !scope.available || !reason.trim() || !confirmed;

  return (
    <Modal
      open={open}
      onClose={onCancel}
      className="m-auto w-[min(640px,94vw)] rounded-[3px] border border-[#4a4128] bg-panel p-0 text-tx shadow-[0_24px_70px_rgba(0,0,0,0.7)]"
    >
      <div className="flex items-start justify-between border-b border-line px-[22px] pt-5 pb-3.5">
        <div>
          <span className="eyebrow">CHANGE SET ROLLBACK</span>
          <h2 className="mt-1 text-[16px] font-semibold text-cream">回滚整个 change set</h2>
          <p className="mt-1 text-[11.5px] text-tx3">
            {roundLabel} · 整 change set 一体回滚，不提供单仓选择（单仓回滚会撕裂联调一致性）
          </p>
        </div>
        <button className="text-[18px] text-tx2" aria-label="关闭" onClick={onCancel}>
          ×
        </button>
      </div>

      <div className="px-[22px] py-4">
        {scope === null && <p className="text-[12.5px] text-tx2">回滚范围取用中…</p>}

        {scope !== null && !scope.available && (
          <div className="mb-3 border-l-2 border-line bg-panel-2 px-3 py-2 text-[12px] leading-[1.7] text-tx2">
            <b className="mr-1.5 font-mono tracking-[0.08em] text-tx3">无可回滚项</b>
            {scope.unavailable_reason
              ? ROLLBACK_UNAVAILABLE_LABEL[scope.unavailable_reason]
              : "服务端报告本轮无可回滚项，但没有给出原因。"}
          </div>
        )}

        {rows.length > 0 && (
          <>
            <div className="microlabel pb-1.5">回滚范围（每仓一行，动作按逆序执行）</div>
            <div className="rounded-hard border border-line">
              {rows.map((row) => (
                <div
                  key={row.repository_id}
                  className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 border-b border-panel px-3 py-2 last:border-b-0"
                >
                  <span className="font-mono text-[11.5px] text-cream">{row.name}</span>
                  <span
                    className={`rounded-hard border px-1.5 font-mono text-[10px] ${ROLLBACK_STATE_SKIN[row.state]}`}
                  >
                    {ROLLBACK_STATE_LABEL[row.state]}
                  </span>
                  <span
                    className={`rounded-hard border px-1.5 font-mono text-[10px] ${ROLLBACK_ACTION_SKIN[row.action]}`}
                  >
                    {/* 第 k 步是服务端 `step` 原值，不是行号 */}
                    {ROLLBACK_ACTION_LABEL[row.action]}
                    {row.step !== null ? ` · 逆序第 ${row.step} 步` : ""}
                  </span>
                  <span className="ml-auto font-mono text-[10.5px] text-tx3">
                    {row.merge_sha
                      ? `merge ${row.merge_sha.slice(0, 12)}`
                      : row.pull_request_number !== null
                        ? `PR #${row.pull_request_number}`
                        : "未发布候选"}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}

        {/* 诚实口径的核心一条：不许诺「一键干净还原」。 */}
        <div className="mt-3 border-l-2 border-amber bg-panel-2 px-3 py-2 text-[12px] leading-[1.7] text-kraft">
          <b className="mr-1.5 font-mono tracking-[0.08em] text-amber">这不是一键干净还原</b>
          已 merge 的仓库靠 <b className="text-cream">revert PR</b> 撤销，而 revert 是 base
          分支上的真实提交：它<b className="text-cream">仍要过自己的 CI 与门禁</b>，不会因为
          回滚已获批准就免检。revert 与后续提交冲突时，恢复 saga
          会<b className="text-cream">自动开一个冲突处理任务</b>交给 Worker，那一步要人和
          agent 一起解，不会自己消失。未 merge 的仓库才是免费撤回（关掉 PR，base 分支上
          什么也没发生过）。
        </div>

        {scope?.recovery_in_progress && (
          <div className="mt-2.5 border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] leading-[1.7] text-[#e8a184]">
            <b className="mr-1.5 font-mono tracking-[0.08em]">已有恢复计划在执行</b>
            本 ChangeSet 上还有未完成的恢复计划。换一个理由再提交会被服务端以 409
            拒绝；重复提交同一份表单则是重放（后端零写入）。
          </div>
        )}

        <label className="mt-3 block">
          <span className="mb-[5px] block text-[12px] font-semibold text-cream">
            回滚理由（写进治理决策，必填）
          </span>
          <textarea
            rows={3}
            className="w-full resize-none rounded-hard border border-line bg-panel-2 px-2.5 py-2 font-sans text-[12.5px] text-tx"
            placeholder="例：上线后发现数据迁移写坏了历史订单，先撤回再修。"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>

        <div className="mt-2.5 rounded-hard border border-dashed border-line bg-panel-2 px-[11px] py-2">
          <span className="block font-mono text-[9.5px] tracking-[0.1em] text-tx2">决策主体</span>
          <b className="text-[12px] text-cream">{principal.label}</b>
        </div>

        {principal.state === "replay" && (
          <div className="mt-2.5 border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] leading-[1.7] text-[#e8a184]">
            <b className="mr-1.5 font-mono tracking-[0.08em]">回放模式</b>
            回滚会在真实世界里关 PR、开 revert PR、动 base 分支，回放夹具里没有可写的对象。
            提交已禁用——就地伪造一份「已回滚」刷新即消失。加 ?source=live 后可真实提交。
          </div>
        )}

        {principal.state === "missing" && (
          <div className="mt-2.5 border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] leading-[1.7] text-[#e8a184]">
            <b className="mr-1.5 font-mono tracking-[0.08em]">决策主体未接入</b>
            花名册里没有该组织可用的 organization_leader，也没有配置 VITE_GOVERNANCE_AGENT_ID
            覆盖。提交已禁用——回滚必须记在一个真实主体名下。§4.6 还要求主体是
            <b className="text-cream"> 组织 leader</b>：这条命令替所有仓库说话，单仓 leader
            会被服务端以 403 拒绝。
          </div>
        )}

        {errorText && (
          <div className="mt-2.5 border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] leading-[1.7] break-words text-[#e8a184]">
            <b className="mr-1.5 font-mono tracking-[0.08em]">服务端拒绝</b>
            {errorText}
          </div>
        )}

        <label className="mt-3 flex items-start gap-2 text-[12px] text-tx2">
          <input
            type="checkbox"
            className="mt-0.5 accent-amber"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          <span>
            我确认对上表 {actionable.length} 个仓库整体回滚，并且知道 revert PR 需要另行过 CI
            与门禁、冲突会转为人工处理任务。
          </span>
        </label>
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
          onClick={() => onConfirm(reason)}
        >
          {submitting
            ? "提交中…"
            : principal.state === "resolving"
              ? "解析主体…"
              : "提交回滚决策"}
        </button>
      </div>
    </Modal>
  );
}
