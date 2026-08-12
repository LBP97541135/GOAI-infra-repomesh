import type { DeliveryTaskView, RedispatchScope } from "../api/contract";
import { eventTime, isRedispatchable, isRerunnable, shortId } from "../display";
import { Modal } from "./Modal";

/** 「重新派工」确认弹窗（契约 v0.4 §8.7.4，缺陷 A-13）。
 *
 *  为什么需要这个按钮：派工是一次性事件、不是可收敛的状态。任务包写进共享存储、
 *  房间里发一条点名，之后 agent 那**一个回合**就是全部机制。那个回合没干成——
 *  容器被重建导致新 Matrix 会话看不见历史消息、任务包拉取被存储权限拒绝、点名
 *  发给了一个容器还不存在的收件人——没有任何一层会发现。控制台就永远停在
 *  running，agent 那边一直闲着。
 *
 *  **这个弹窗的职责是把「重发到底做了什么」说清楚，而不是替人判断该不该发。**
 *  三句话对应服务端三件事，一句不多：
 *   ① 重发任务包与点名；② 不会重复建任务；③ 对已在工作的 agent 表现为一条重复通知。
 *
 *  零影子判断（§3.1）：入口的出现条件只是「本轮有非终态任务」——这是读模型的
 *  `display_status` 原值，不是界面推出来的「卡住了」。什么时候该用，由文案说明、
 *  由人决定。「上次派工」时间是**事实**（服务端 `last_dispatched_at`），不是判据：
 *  界面不拿它减当前时间去得出一个结论。 */

export function RedispatchModal({
  open,
  roundLabel,
  tasks,
  scope,
  submitting,
  errorText,
  onScopeChange,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  roundLabel: string;
  /** 本轮全部任务原文；弹窗自己分「会重发的」与「已终态不动的」 */
  tasks: DeliveryTaskView[];
  scope: RedispatchScope;
  submitting: boolean;
  /** 服务端 detail 原文（409 / 503）。**不翻译不软化**，原样贴出来 */
  errorText: string | null;
  onScopeChange: (scope: RedispatchScope) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const unfinished = tasks.filter(isRedispatchable);
  const rerunnable = tasks.filter(isRerunnable);
  const rerun = scope === "rerun";
  const live = rerun ? [...unfinished, ...rerunnable] : unfinished;
  const settled = tasks.filter((t) => !live.includes(t));
  const neverDispatched = live.filter((t) => t.last_dispatched_at === null);

  return (
    <Modal
      open={open}
      onClose={onCancel}
      className="m-auto w-[min(560px,92vw)] rounded-[3px] border border-[#4a4128] bg-panel p-0 text-tx shadow-[0_24px_70px_rgba(0,0,0,0.7)]"
    >
      <div className="flex items-start justify-between border-b border-line px-[22px] pt-5 pb-3.5">
        <div>
          <span className="eyebrow">ROUND RE-DISPATCH</span>
          <h2 className="mt-1 text-[16px] font-semibold text-cream">重新派工 · {roundLabel}</h2>
        </div>
        <button className="text-[18px] text-tx2" aria-label="关闭" onClick={onCancel}>
          ×
        </button>
      </div>

      <div className="px-[22px] py-4">
        {/* 两个范围是两种风险，所以是两个显式选项而不是一个开关的默认值。
            上面那个不写任何任务行；下面那个会写。文案分别说清楚代价。 */}
        <div className="mb-3 flex flex-col gap-1.5">
          {(
            [
              [
                "unfinished",
                `只重发未完成的（${unfinished.length}）`,
                "不动任何任务行。按早了的代价：房间里多一条重复通知。",
              ],
              [
                "rerun",
                `连同已完成的一起重做（+${rerunnable.length}）`,
                "会把已完成的任务送回去重做：清掉它们已记录的结论，本批次重新变成未完成。用于「结果本身不对」——比如跑完了却没有测试结果，导致交付一直被拒。",
              ],
            ] as Array<[RedispatchScope, string, string]>
          ).map(([value, label, hint]) => (
            <label
              key={value}
              className={`flex cursor-pointer gap-2 rounded-hard border px-2.5 py-2 ${
                scope === value ? "border-amber bg-panel-2" : "border-line"
              }`}
            >
              <input
                type="radio"
                name="redispatch-scope"
                className="mt-0.5 flex-none accent-[#c8952a]"
                checked={scope === value}
                disabled={value === "rerun" && rerunnable.length === 0}
                onChange={() => onScopeChange(value)}
              />
              <span className="min-w-0">
                <b className="block text-[12px] text-cream">{label}</b>
                <span className="block text-[11px] leading-[1.65] text-tx3">{hint}</span>
              </span>
            </label>
          ))}
        </div>

        {/* 三句话就是服务端做的三件事，逐条对应，不多不少。 */}
        <p className="text-[12.5px] leading-[1.75] text-tx">
          将对本轮 <b className="text-cream">{live.length} 个任务</b>
          重发<b className="text-cream">任务包与点名</b>；
          <b className="text-cream">不会重复建任务</b>；
          对已在工作的 agent，这表现为<b className="text-cream">一条重复通知</b>
          {rerun && rerunnable.length > 0 ? (
            <>
              。其中 <b className="text-cream">{rerunnable.length} 个已完成的任务会被送回去重做</b>
              ——这一条是<b className="text-cream">真的写</b>，它们已记录的结论会被清掉。
            </>
          ) : (
            "。"
          )}
        </p>

        <div className="mt-3 rounded-hard border border-line bg-panel-2 px-2.5 py-2">
          <div className="pb-1 font-mono text-[9.5px] tracking-[0.1em] text-tx2">将重发</div>
          {live.length === 0 ? (
            <p className="text-[11.5px] text-tx3">本轮没有未完成的任务。</p>
          ) : (
            live.map((t) => (
              <div key={t.task_id} className="flex flex-wrap items-baseline gap-x-2 py-0.5">
                <span className="rounded-hard border border-line px-1.5 font-mono text-[10px] text-tx2">
                  {t.display_status}
                </span>
                <span className="min-w-0 flex-1 truncate text-[11.5px] text-kraft">{t.title}</span>
                {/* agent 是 AgentTeams 资源名不是人名；解析不到就说 id */}
                <span className="font-mono text-[10.5px] text-tx3">
                  {t.agent ?? `AGENT ${shortId(t.task_id)}`}
                </span>
                <span className="text-[10.5px] text-tx3">
                  {t.last_dispatched_at === null ? "从未派工" : eventTime(t.last_dispatched_at)}
                </span>
              </div>
            ))
          )}
        </div>

        {settled.length > 0 && (
          <p className="mt-2 text-[11.5px] leading-[1.7] text-tx3">
            另有 {settled.length} 个任务<b className="text-tx2">不会被动</b>——
            {rerun ? (
              <>
                它们已取消或被新版计划替换，
                <b className="text-tx2">即使选「一起重做」也不会被重开</b>
                ：那是「决定不做」，不是「做错了」。
              </>
            ) : (
              <>
                它们已经完成。其中
                {rerunnable.length > 0 ? (
                  <b className="text-tx2">有 {rerunnable.length} 个可以用上面第二档送回去重做</b>
                ) : (
                  <b className="text-tx2">没有可重做的</b>
                )}
                。服务端按后端状态判定，不是界面挑的。
              </>
            )}
          </p>
        )}

        {neverDispatched.length > 0 && (
          <p className="mt-2 text-[11.5px] leading-[1.7] text-kraft">
            其中 {neverDispatched.length} 个<b className="text-cream">从来没有派工记录</b>
            ：这一轮的派工消息压根没写下来过，而不是发了没人理。
          </p>
        )}

        {/* 「什么时候该用」写在这里，而不是做成一个界面自己算出来的「卡住了」徽标。
            两档的适用场景与代价都不同，所以分别说，不合并成一句通用的。 */}
        <div className="mt-3 border-l-2 border-amber bg-panel-2 px-3 py-2 text-[12px] leading-[1.7] text-kraft">
          <b className="mr-1.5 font-mono tracking-[0.08em] text-amber">什么时候用</b>
          {rerun ? (
            <>
              任务报告了完成、但<b className="text-cream">结果本身不对</b>时——比如 runner 跑完了却
              没有测试结果，交付因此一直被拒、后台在静默重试。先修好那个条件（例如补上测试命令），
              再用这一档让工作真的重做一遍。
              <b className="text-cream">这会清掉已记录的结论、让本批次重新变成未完成</b>
              ，所以它不是默认档。
            </>
          ) : (
            <>
              任务长时间停在 pending/running、而房间里看不到 agent 有任何动静时。
              界面<b className="text-cream">不替你判断「卡住了」</b>——只要还有未完成的任务，这个入口就在，
              用不用你说了算。发早了唯一的代价是一条重复通知。
            </>
          )}
        </div>

        {errorText && (
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
          disabled={submitting || live.length === 0}
          onClick={onConfirm}
        >
          {submitting ? "重发中…" : "确认重新派工"}
        </button>
      </div>
    </Modal>
  );
}
