import type { ProjectExecutionMode, TopologyPolicyDraftView } from "../api/humanControl";
import {
  CONTROL_ACTION_ORDER,
  POLICY_TIER_TITLE,
  ROLE_LABEL,
  checkpointLabel,
  controlActionLabel,
  orderByFixed,
  orderCheckpoints,
  shortId,
  type PolicyTier,
} from "../display";

/** 发现链面板底部的「监管策略」卡片（迁移 5-1b · F4，设计文档 §3.1/§3.2）。
 *
 *  **它为什么不进步进器**：面板里那条注释（`DiscoveryPanel.tsx`）把判据说清楚了——
 *  发现四步改的是同一份草稿快照，物化建的是执行面的实体，所以物化不是第五步。
 *  配策略按同一条判据本可以算一步（它改的也是草稿、也不建实体），但它**不属于
 *  「发现」这件事**：发现回答「要动哪些仓库」，策略回答「谁来盯着」。两件事共用
 *  一个步进器，「走到第几步」这个唯一事实源就要为一件与发现无关的事让路。
 *  所以：物化区上方一张卡片，与物化按钮同级，步进器仍是四格。
 *
 *  **未设那句话不是警告，是陈述**。它就是不设时的真实后果（`auto` 时后端
 *  `requires_human_checkpoint` 恒返回 false，一张审核单都不会开）。全自动是默认值、
 *  不是故障，所以这里没有告警色也没有感叹号——活体库 14 个项目里 12 个正是这样跑的，
 *  而**没有任何一个用户被问过**。这批迁移要改的是「没人被问过」，不是把默认值画成红的。 */

/** 草稿卡片的取数态 + 两个「不该有卡片」的门态。
 *
 *  **`sealed` 与 `unknown` 不是错误态，是「这个问题已经不归草稿管了」**（§3.4）：
 *  `GET /projects/{id}/topology` 拿得到真档案时，5-1a 的只读段已经在显示它，
 *  草稿卡片不该再出现——不给一个按了也没用的按钮。 */
export type PolicyDraftState =
  | { kind: "loading" }
  /** 草稿端点 404 = **还没设过**。这不是错误，是「未设定」这条事实本身。 */
  | { kind: "unset" }
  | { kind: "set"; draft: TopologyPolicyDraftView }
  /** 401：会话过期。**不给重试**——重试一个过期会话不会有别的结果。 */
  | { kind: "unauthenticated"; detail: string }
  /** 403：既不是管理员、也不在这份草稿的授权名单里。草稿可能存在，只是读不到。 */
  | { kind: "forbidden"; detail: string }
  | { kind: "error"; message: string }
  /** 已有真档案：草稿窗口已关（§3.4）。 */
  | { kind: "sealed" }
  /** 拓扑取数未落定或失败（含回放模式）：**不知道有没有档案**，不猜。 */
  | { kind: "unknown" };

/** `modeOf` 的逆（设计文档 §4.8 的映射表反着读）。界面三档与后端三个取值一一对应：
 *  `auto` ↔ 第一档、`manual_controlled` ↔ 第三档（域强制六个卡点全齐）、
 *  其余即 `supervised` ↔ 第二档。
 *
 *  措辞取**共用那一份**（`display.ts` 的 `POLICY_TIER_TITLE`，弹窗用的是同一张表），
 *  不另立一份：用户在弹窗里选的是
 *  「关键处我看一眼」，回到卡片必须还叫「关键处我看一眼」，否则同一个决定在选它的
 *  地方和看它的地方是两个名字。 */
function tierOfMode(mode: ProjectExecutionMode): PolicyTier {
  if (mode === "auto") return "unattended";
  return mode === "manual_controlled" ? "every_step" : "key_points";
}

/** 一份草稿的摘要。**卡片与物化弹窗共用这一个渲染**（§3.2 与 §3.3 是同一份策略的
 *  两次露出），两处各写一份就会出现「卡片说三个卡点、弹窗说两个」这种同屏矛盾。 */
export function PolicyDigest({ draft }: { draft: TopologyPolicyDraftView }) {
  // ⚠ `required_checkpoints` 回来的是 **frozenset 的迭代顺序**（进程内哈希随机化，
  // 同一份草稿两次回读可以不一样）。渲染前一律过 `orderCheckpoints` 按流程先后定序，
  // 否则同一份策略每次刷新卡点次序都在跳。
  const ordered = orderCheckpoints(draft.required_checkpoints);
  const auto = draft.execution_mode === "auto";

  return (
    <>
      <p className="text-[12.5px] leading-[1.7] text-tx">
        <b className="text-cream">{POLICY_TIER_TITLE[tierOfMode(draft.execution_mode)]}</b>
        <span className="text-tx2">
          {" · "}
          {ordered.length === 0 ? "没有任何人工卡点" : `${ordered.length} 个人工卡点`}
        </span>
      </p>

      {ordered.length > 0 && (
        <div className="mt-1 flex flex-wrap items-baseline gap-1.5">
          {ordered.map((checkpoint) => {
            // 「规格」当前没有可达的触发点（设计文档 §4.3.1 三环查证：它的 evaluate 被
            // 「非 TASK 规格」守着，而全仓唯一的发布方发的恒是 TASK 规格）。勾了也不会停
            // ——把它和真会停的卡点画成同一个样子，就是在这张摘要上撒谎。
            const inert = checkpoint === "specification";
            const forced = checkpoint === "exception_escalation";
            return (
              <span
                key={checkpoint}
                className={`rounded-hard border px-2 py-px text-[11px] ${
                  inert ? "border-line text-tx3" : "border-amber text-amber"
                }`}
                title={checkpoint}
              >
                {checkpointLabel(checkpoint)}
                {forced && <span className="text-tx3">（强制）</span>}
                {inert && <span>（当前无触发点）</span>}
              </span>
            );
          })}
        </div>
      )}

      <div className="mt-1.5">
        {draft.human_grants.length === 0 ? (
          <p className="text-[11.5px] leading-[1.7] text-tx3">
            {auto
              ? "全自动不设审核人——没有任何一步会停下来等人，「谁来看」这一问对它没有意义。"
              : "这份草稿没有授权人。"}
          </p>
        ) : (
          <>
            <span className="text-[11.5px] text-tx2">
              审核人 {new Set(draft.human_grants.map((grant) => grant.human_principal_id)).size} 位
              {/* 一个人可以有多条授权（域按「人 + 仓库」这一对去重），所以人数与条数不是一回事 */}
              {draft.human_grants.length !==
                new Set(draft.human_grants.map((grant) => grant.human_principal_id)).size &&
                ` · ${draft.human_grants.length} 条授权`}
            </span>
            {draft.human_grants.map((grant, index) => (
              // key 用下标兜底：域按「人 + 仓库」去重，但同一人的项目级授权 repository_id
              // 恒为 null，拿这一对拼 key 仍可能撞上
              <div
                key={`${grant.human_principal_id}:${index}`}
                className="mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-1"
              >
                {/* 只有 UUID，没有人名——补人名要调只有管理员能用的 `GET /auth/accounts`，
                    那会让同一份策略对两个账号显示成两个样子（5-1a 已就此裁决，这里照办）。
                    短版够认，完整 id 挂 title 供复制核对。 */}
                <span className="font-mono text-[11px] text-tx" title={`账号 ${grant.human_principal_id}`}>
                  {shortId(grant.human_principal_id)}
                </span>
                <span className="text-[11px] text-tx3">{ROLE_LABEL[grant.role] ?? grant.role}</span>
                <span className="text-[11px] text-tx2">
                  {/* control_actions 同样是 frozenset，按展示次序定序后再拼 */}
                  {orderByFixed(grant.control_actions, CONTROL_ACTION_ORDER)
                    .map(controlActionLabel)
                    .join(" · ")}
                </span>
                {grant.repository_id !== null && (
                  <span className="font-mono text-[10.5px] text-tx3">限仓 {shortId(grant.repository_id)}</span>
                )}
              </div>
            ))}
          </>
        )}
      </div>
    </>
  );
}

/** 物化区上方的策略卡片。**只在「拓扑还不存在」时渲染**，那一判由 issue 详情页的
 *  拓扑取数给出（本卡片不自己再问一遍——两个取数点会在物化那一瞬间给出互相矛盾的
 *  答案）。 */
export function SupervisionPolicyCard({
  state,
  onConfigure,
  onRetry,
}: {
  state: PolicyDraftState;
  onConfigure: () => void;
  onRetry: () => void;
}) {
  // 401/403/断网在弹窗里各有专门处置，这里只用一句话说清「为什么没有按钮」，
  // 不在外层再抄一套（抄出来的第二套会与弹窗漂移）。
  const actionable = state.kind === "unset" || state.kind === "set";

  return (
    <div className="mt-3 rounded-hard border border-line bg-panel px-3.5 py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="microlabel pb-1.5">监管策略</div>

          {state.kind === "loading" && (
            <p className="text-[12px] text-tx2">监管策略草稿读取中…</p>
          )}

          {state.kind === "unset" && (
            <>
              {/* §3.2：这句话不是警告，是陈述——它就是不设时的真实后果。
                  没有感叹号、没有告警色：全自动是默认值，不是故障。 */}
              <p className="text-[12.5px] leading-[1.75] text-tx">
                本次将以 <b className="text-cream">全自动</b> 运行，没有任何人工卡点。
                这个需求不会产生任何审核待办。
              </p>
              <p className="mt-1 text-[11px] leading-[1.7] text-tx3">
                要设就在按「物化并开工」之前：首次物化时后端会按这里的设定建出项目档案，
                而全仓没有任何更新档案的端点——过了那一步，这个需求的监管策略就定死了。
              </p>
            </>
          )}

          {state.kind === "set" && <PolicyDigest draft={state.draft} />}

          {state.kind === "unauthenticated" && (
            <p className="text-[11.5px] leading-[1.7] text-tx2">
              {state.detail}
              <span className="block text-tx3">
                监管策略认的是本地登录会话，不是页面其余部分用的动作 token，所以别处还读得到、
                只有这里读不到。重新登录后再来——<b className="text-tx2">这里不给重试按钮</b>，
                重试一个过期的会话不会有别的结果。
              </span>
            </p>
          )}

          {state.kind === "forbidden" && (
            <p className="text-[11.5px] leading-[1.7] text-tx2">
              {state.detail}
              <span className="block text-tx3">
                当前账号既不是管理员、也不在这份草稿的授权名单里，读不到它。
                <b className="text-tx2">读不到不等于没有</b>——设定监管策略要本地管理员，
                请用管理员账号登录，或让管理员来设。
              </span>
            </p>
          )}

          {state.kind === "error" && (
            <p className="text-[11.5px] leading-[1.7] text-salmon">
              监管策略草稿取用失败：{state.message}
              <button className="pl-2 text-tx2 underline hover:text-amber-hi" onClick={onRetry}>
                重试
              </button>
              <span className="block text-tx3">
                取不到时<b className="text-tx2">不猜</b>「多半没设」：猜错的那一半会把
                「设过了」显示成「没设过」，而这两者的后果完全相反。
              </span>
            </p>
          )}
        </div>

        {actionable && (
          <button
            type="button"
            className="flex-none rounded-hard border border-line px-3.5 py-1.5 text-[12.5px] text-tx2 hover:border-amber hover:text-amber-hi"
            onClick={onConfigure}
          >
            {state.kind === "set" ? "修改" : "配置"}
          </button>
        )}
      </div>
    </div>
  );
}
