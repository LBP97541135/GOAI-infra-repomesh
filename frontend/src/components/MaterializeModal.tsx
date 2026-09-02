import { useEffect, useState } from "react";
import type { ExternalMembersNotReadyDetail, MemberReadinessFact } from "../api/contract";
import { defaultClient } from "../api/client";
import { stalePidFile, startMembers, type StalePidFileDetail } from "../api/launcher";
import { READINESS_LABEL, READINESS_SKIN, errText, eventTime, shortId } from "../display";
import { Modal } from "./Modal";
import { StalePidBlock } from "./StatusBlocks";
import type { ApprovalPrincipal } from "./DiscoveryApproval";
import { PolicyDigest, type PolicyDraftState } from "./SupervisionPolicyCard";

/** 预检三态。`failed` 不是「不就绪」——那是**没问出来**，两者的下一步完全不同。 */
type Precheck =
  | { kind: "loading" }
  | { kind: "checked"; checkedAt: string; members: MemberReadinessFact[] }
  | { kind: "failed"; message: string };

/** 成员逐行。预检与 409 用同一个组件，因为它们本来就是同一个 wire 形状——
 *  服务端把这两处收敛到同一个 helper，界面再分成两套渲染就白收敛了。 */
function ReadinessRows({ members }: { members: MemberReadinessFact[] }) {
  return (
    <ul className="mt-1.5">
      {members.map((member) => (
        <li key={member.agentId} className="flex flex-wrap items-baseline gap-x-2 border-b border-line py-1 last:border-b-0">
          <span className={`rounded-hard border px-1.5 py-px text-[10px] ${READINESS_SKIN[member.status]}`}>
            {READINESS_LABEL[member.status]}
          </span>
          <span className="font-mono text-[11px] text-tx">
            {member.role} · {shortId(member.agentId)}
          </span>
          {/* 服务端英文原句，不翻译：它是这一行状态的唯一解释，改写就成了转述 */}
          <span className="text-[11px] text-tx3">{member.reason}</span>
        </li>
      ))}
    </ul>
  );
}

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
 *  除了数出来，弹窗还**挡一件事**：本轮要派工的成员里有跑在操作者机器上的 CLI，
 *  它们不在平台的进程里。开窗即做一次无副作用的就绪预检，不就绪就禁用提交并给出
 *  「启动并重新检查 / 仅重新检查」。预检只是提前告知——权威是物化里那道门，
 *  撞上它时的 409 在下方按同一份形状逐成员摊开。
 *
 *  弹窗壳沿用 aefd39a 的统一 Modal（Esc + 焦点陷阱 + 关闭即卸载），配色全部取
 *  index.css 既有令牌，不新增颜色语义。 */
export function MaterializeModal({
  open,
  issueId,
  planVersion,
  taskCount,
  teamCount,
  unresolvedCount,
  policy,
  principal,
  submitting,
  errorText,
  notReady,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  /** 就绪预检的主语：成员集合由服务端按本轮计划的仓库派生，前端不传名单 */
  issueId: string;
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
  /** 物化 409 里唯一结构化的那一族（本机 CLI 未就绪）。非该族时 null，走 errorText */
  notReady: ExternalMembersNotReadyDetail | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const [precheck, setPrecheck] = useState<Precheck>({ kind: "loading" });
  const [checkSeq, setCheckSeq] = useState(0);
  /** 启动器那一侧的失败。与预检失败分开：一个是「没起来」，一个是「问不到」。
   *  PID 占位那一族单独留一份结构化的——它的解法是逐个文件名，`errText` 会把它
   *  压成一串截断的 JSON，而被切掉的正好是文件名。 */
  const [launcherError, setLauncherError] = useState<string | null>(null);
  const [launcherStale, setLauncherStale] = useState<StalePidFileDetail | null>(null);

  // 关掉再打开是一次新的决定：上一次开着时启动器说的话不该跟着回来
  useEffect(() => {
    if (!open) return;
    setLauncherError(null);
    setLauncherStale(null);
  }, [open]);

  /** 开弹窗即预检，之后只由两个按钮驱动——**不轮询**。这是一次「按下之前先看一眼」，
   *  不是状态板；本地 CLI 页才是那个每 5 秒刷新的地方。 */
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setPrecheck({ kind: "loading" });
    defaultClient()
      .getDiscoveryReadiness(issueId)
      .then((view) => !cancelled && setPrecheck({ kind: "checked", ...view }))
      .catch((err: unknown) => !cancelled && setPrecheck({ kind: "failed", message: errText(err) }));
    return () => {
      cancelled = true;
    };
  }, [open, issueId, checkSeq]);

  /** 服务端刚以「未就绪」拒绝过，说明上面那份预检答案已经旧了——就地重查一次。
   *  这同时把两个按钮带回来：它们只长在「不就绪」那一支上，而预检若还停在
   *  「全部就绪」，读者会看见一句已被推翻的话，且没有任何可按的东西。 */
  useEffect(() => {
    if (notReady) setCheckSeq((n) => n + 1);
  }, [notReady]);

  const bumpCheck = () => setCheckSeq((n) => n + 1);

  /** 「仅重新检查」。**顺手收起启动器上一次说的话**：按这个按钮的人，多半正是照着
   *  那段话去删了 PID 文件才回来的，留着它就是一段已经过期的红字杵在绿色预检旁边。 */
  const recheck = () => {
    setLauncherError(null);
    setLauncherStale(null);
    bumpCheck();
  };

  /** 启动全部 + 重新检查。启动期间本分支整块换成「检查中…」，两个按钮随之消失——
   *  不必再造一个 busy 位来禁用它们。启动失败也照样重查：最常见的失败就是「已经在跑了」。
   *  收尾走 `bumpCheck` 而不是 `recheck`：后者会把上面那个 catch 刚记下的失败擦掉。 */
  const startAndRecheck = () => {
    setLauncherError(null);
    setLauncherStale(null);
    setPrecheck({ kind: "loading" });
    startMembers()
      .catch((err: unknown) => {
        setLauncherStale(stalePidFile(err));
        setLauncherError(errText(err));
      })
      .finally(bumpCheck);
  };

  const notReadyCount =
    precheck.kind === "checked" ? precheck.members.filter((m) => m.status !== "ready").length : 0;
  /** 提交的门。**预检失败不算门**：那个端点是「仅供参考」，真正的判定在物化里那道
   *  门——它在真要派工的一刻重读同一批事实。凭一次读不到就把按钮焊死，等于让一个
   *  advisory 端点掉线就停掉整条链。 */
  const blocked = principal.state !== "ready" || precheck.kind === "loading" || notReadyCount > 0;

  /** 那份拒绝是否已被后来的预检**逐条**推翻。推翻了就不再显示：把「重试不会让它变好」
   *  摆在一个已经可以按的确认键旁边，读者只会照着它不去按；拒绝的原文（errorText）
   *  一起收起——它是同一份 409 的字符串化版本。
   *
   *  **判据不能是「预检绿了」，因为两个成员集合不是同一个来路**：预检从**目录**派生
   *  （活跃、且角色能由 Bridge 承担的主体），门从**拓扑**派生，两者会分叉——一个还在
   *  拓扑里、主体已不再 ACTIVE 的成员会被门拒绝，却压根不在预检问过的名单上。此时
   *  绿色（甚至空）的预检什么也没证明，据此收起拒绝，留给读者的就是一个能按、按下去
   *  又 409、且没有任何解释的确认键。
   *
   *  所以逐条要求：被拒的每个成员都在这次预检里出现过，且这次答的是 ready。集合一分叉，
   *  这条自然落回「继续显示拒绝」——不确定时显示旧拒绝，是这两个方向里可回退的那个。 */
  const refusalOverturned =
    notReady !== null &&
    precheck.kind === "checked" &&
    notReady.members.length > 0 &&
    notReady.members.every((refused) =>
      precheck.members.some((row) => row.agentId === refused.agentId && row.status === "ready"),
    );

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

        {/* ── 本地 CLI 就绪预检 ───────────────────────────────────────────
            **这一段不是提示而是门**：本轮要派工的成员里有跑在操作者机器上的 CLI，
            它们不在 RepoMesh 的进程里，物化那一刻起不来就是一批派不出去的任务。
            预检把「按下去才知道」换成「按下去之前就知道该起哪台机器」——但它只是
            提前告知，权威始终是物化里那道门（下方 409 就是它说话）。 */}
        <div className="mt-3 rounded-hard border border-line bg-panel-2 px-3 py-2.5">
          <span className="block font-mono text-[9.5px] tracking-[0.1em] text-tx2">本地 CLI 就绪</span>
          {precheck.kind === "loading" ? (
            <p className="mt-1 text-[12px] text-tx2">检查中…</p>
          ) : precheck.kind === "failed" ? (
            <p className="mt-1 text-[12px] leading-[1.7] text-tx3">
              这一次没取到就绪预检（{precheck.message}）。
              <b className="text-tx2">提交没有因此禁用</b>
              ：预检只是提前告知，真正的判定在物化里那道门——它会在真要派工的一刻重读同一批事实，
              不就绪就以 409 拒绝，那时这里会列出是谁。
            </p>
          ) : notReadyCount > 0 ? (
            <>
              <p className="mt-1 text-[12px] leading-[1.75] text-tx">
                <b className="text-cream">{notReadyCount} 个</b>成员还不能接活，提交已禁用。
                这些是跑在<b className="text-cream">你自己机器上</b>的 CLI，平台起不了它们。
              </p>
              <ReadinessRows members={precheck.members} />
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  className="rounded-hard border border-amber/60 px-2.5 py-1 text-[11.5px] text-amber hover:bg-amber/10 hover:text-amber-hi"
                  onClick={startAndRecheck}
                >
                  启动并重新检查
                </button>
                <button
                  className="rounded-hard border border-line px-2.5 py-1 text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
                  onClick={recheck}
                >
                  仅重新检查
                </button>
              </div>
              <p className="mt-1.5 text-[11px] leading-[1.7] text-tx3">
                「启动并重新检查」调的是本机启动器（127.0.0.1:8121）；它没在跑时这里会说连不上，
                改用「本地 CLI」页的命令行入口起完再回来点「仅重新检查」。
              </p>
            </>
          ) : precheck.members.length === 0 ? (
            <p className="mt-1 text-[12px] leading-[1.7] text-tx2">
              本轮没有本机 CLI 成员参与（这些仓库的成员都由平台托管）· 检查时间{" "}
              <span className="font-mono">{eventTime(precheck.checkedAt)}</span>
            </p>
          ) : (
            <p className="mt-1 text-[12px] leading-[1.7] text-tx">
              <b className="text-olive">{precheck.members.length} 个成员全部就绪</b> · 检查时间{" "}
              <span className="font-mono">{eventTime(precheck.checkedAt)}</span>
            </p>
          )}
          {launcherStale ? (
            <StalePidBlock detail={launcherStale} className="mt-2" />
          ) : (
            launcherError && (
              <p className="mt-1.5 text-[11px] leading-[1.7] break-words text-[#e8a184]">
                本机启动器：{launcherError}
              </p>
            )
          )}
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

        {refusalOverturned ? null : notReady ? (
          // 同一个 409 家族里唯一结构化的一份：detail 原文在这里是一坨 JSON，而它每个
          // 字段都是解法的一部分。预检过了照样可能撞上它——租约是关于此刻的断言，
          // 从看见到按下之间它会过期。
          <div className="mt-3 border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] leading-[1.7] break-words text-[#e8a184]">
            <b className="mr-1.5 font-mono tracking-[0.08em]">本地 CLI 未就绪</b>
            {notReady.message}
            <ReadinessRows members={notReady.members} />
            <p className="mt-1.5 text-[11px]">
              重试不会让它变好：先把这些成员起来（上方「启动并重新检查」，或「本地 CLI」页），
              再确认物化。幂等键还是同一把，这不算第二次物化。
            </p>
          </div>
        ) : (
          errorText && (
            // 服务端 detail 原文。409 的原因不止一种（受控项目的 REPOSITORY_SCOPE
            // 检查点未过、计划尚未生成…），归并成一句「物化失败」会把可自助解决的
            // 前置问题伪装成系统故障。
            <div className="mt-3 border-l-2 border-salmon bg-[#2b1712] px-3 py-2 text-[12px] leading-[1.7] break-words text-[#e8a184]">
              <b className="mr-1.5 font-mono tracking-[0.08em]">服务端拒绝</b>
              {errorText}
            </div>
          )
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
          {submitting
            ? "物化中…"
            : principal.state === "resolving"
              ? "解析主体…"
              : precheck.kind === "loading"
                ? "就绪检查中…"
                : "确认物化并开工"}
        </button>
      </div>
    </Modal>
  );
}
