import { useState } from "react";
import type { RepositoryPlan, RoomListItem, RoomStreamItem, RoomStreamPage } from "../data/issueDetail";
import { eventTime } from "../viewmodel";

/** 活体房间视图（CONS-43 骨架）。版式按原型 `#v-room`：
 *  房间头（成员 + LIVE + 刷新机制标注）→ 双视图切换（房间聊天 ↔ DAG·PLAN·SPEC）
 *  → 单仓悬浮环境窗。
 *
 *  ⚠ 本文件承载契约 v0.2 §5.2 + Q4 的**硬约束**：
 *     source === "message" 才是房间内真实发生的消息，渲染为聊天气泡（头像 + 发送者）；
 *     governance / gate / runner 是控制台**投影事实**，必须渲染为系统条目，
 *     **无头像、无发送者名**——不得让用户以为某个 agent 在房间里说过这句话。
 *  这是契约文本要求，不是渲染建议。分流点只有一处（见 StreamEntry），
 *  新增 source 值时也必须落在系统条目一侧，除非契约明写它是真实消息。 */

/** 非 message 源的展示皮肤。配色沿用 CONS-14 事件时间线的既有令牌，零新色值。
 *  bluegray 保留给「执行中」语义，此处不用。 */
const SOURCE_SKIN: Record<Exclude<RoomStreamItem["source"], "message">, { label: string; skin: string }> = {
  governance: { label: "治理决策", skin: "border-kraft text-kraft" },
  gate: { label: "门禁", skin: "border-olive text-olive" },
  runner: { label: "RUNNER", skin: "border-amber text-amber" },
};

function MessageBubble({ item }: { item: RoomStreamItem }) {
  const m = item.message;
  if (!m) return null;
  // sender_name 可能为 null（§4.2 诚实降级）：退到 agent id 短版，不编造名字
  const who = m.sender_name ?? `AGENT ${m.sender_agent_id.slice(0, 8)}`;
  const initial = m.sender_name ? m.sender_name.slice(0, 2).toUpperCase() : "AG";

  return (
    <div className="flex gap-2.5 py-1.5">
      <span className="grid size-7 flex-none place-items-center rounded-hard bg-line font-mono text-[10px] text-amber">
        {initial}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[11.5px] text-amber">{who}</span>
          <span className="rounded-hard border border-kraft px-1.5 font-mono text-[10px] text-kraft">{m.kind}</span>
          <span className="ml-auto font-mono text-[10.5px] text-[#6b6046]">{eventTime(item.at).slice(0, 5)}</span>
        </div>
        <div className="mt-1 rounded-hard border border-line bg-panel px-2.5 py-2 text-[12px] leading-[1.6] text-tx">
          {m.body}
          {m.recipient_name && <span className="text-tx2">（→ {m.recipient_name}）</span>}
        </div>
      </div>
    </div>
  );
}

/** 系统条目：无头像、无发送者，视觉上与气泡明确区分（左侧标尺 + 等宽摘要）。 */
function SystemEntry({ item }: { item: RoomStreamItem }) {
  const skin = SOURCE_SKIN[item.source as Exclude<RoomStreamItem["source"], "message">];
  return (
    <div className="flex items-baseline gap-2.5 border-l-2 border-line py-1.5 pl-2.5">
      <span className={`flex-none rounded-hard border px-1.5 font-mono text-[10px] tracking-[0.08em] ${skin.skin}`}>
        {skin.label}
      </span>
      <span className="min-w-0 flex-1 text-[11.5px] leading-[1.6] text-tx2">{item.text}</span>
      <span className="flex-none font-mono text-[10.5px] text-[#6b6046]">{eventTime(item.at).slice(0, 5)}</span>
    </div>
  );
}

/** 唯一分流点：契约 §5.2 —— 只有 message 是房间内真实发生的消息。 */
function StreamEntry({ item }: { item: RoomStreamItem }) {
  return item.source === "message" ? <MessageBubble item={item} /> : <SystemEntry item={item} />;
}

function PlanPaper({ plan }: { plan: RepositoryPlan }) {
  const focus = plan.dag.nodes.find((n) => n.is_focus);

  return (
    <div className="max-w-[560px] rounded-hard bg-cream px-5 py-4 text-paper-ink">
      <div className="flex items-baseline gap-2.5 border-b-2 border-paper-ink pb-2">
        <span className="bg-paper-ink px-1.5 font-mono text-[11px] tracking-[0.14em] text-cream">REPO PLAN</span>
        <span className="font-mono text-[12.5px] font-bold">{focus?.name ?? "—"}</span>
        <span className="ml-auto font-mono text-[10.5px]">plan v{plan.plan_version}</span>
      </div>

      <div className="pt-3.5 pb-1.5 font-mono text-[11px] font-bold">1.0 REPOSITORY SPEC</div>
      {plan.spec ? (
        <>
          <div className="text-[12px] leading-[1.7]">{plan.spec.goal}</div>
          <div className="pt-2 font-mono text-[10.5px] text-paper-dim">
            {plan.spec.status} · rev {plan.spec.revision} · {plan.spec.kind}
          </div>
          <ul className="pt-1.5 text-[11.5px] leading-[1.7]">
            {plan.spec.acceptance.map((a) => (
              <li key={a}>· {a}</li>
            ))}
          </ul>
          <div className="pt-1.5 font-mono text-[10.5px] text-paper-dim">
            allowed {plan.spec.allowed_paths.join(" ")} · forbidden {plan.spec.forbidden_paths.join(" ") || "—"}
          </div>
        </>
      ) : (
        /* §5.4：无匹配 spec 时的既定文案，不回填项目级契约冒充本仓 spec */
        <div className="text-[12px] leading-[1.7] text-paper-dim">本仓无独立 spec，适用项目工程契约。</div>
      )}

      <div className="pt-4 pb-1.5 font-mono text-[11px] font-bold">2.0 REPOSITORY DAG</div>
      <div className="rounded-hard border border-[#b7a87e] px-3 py-2.5 font-mono text-[11.5px] leading-[1.9]">
        {plan.execution_batches.map((batch, i) => (
          <div key={batch.join("|")}>
            <span className="text-paper-dim">批次 {i + 1}　</span>
            {batch.map((name) => {
              const node = plan.dag.nodes.find((n) => n.name === name);
              return (
                <span key={name} className={node?.is_focus ? "font-bold" : "text-paper-dim"}>
                  {name}
                  {node?.is_focus ? " ◂ 本仓" : ""}
                  {"　"}
                </span>
              );
            })}
          </div>
        ))}
        <div className="pt-1.5 text-[10.5px] leading-[1.5] text-paper-dim">
          {plan.dag.edges.length} 条依赖边 · 粒度 {plan.dag.granularity} · 来源 {plan.dag.edge_source}
        </div>
      </div>

      <div className="pt-3.5 text-[10.5px] leading-[1.6] text-paper-dim">
        §5.5：`plan_snapshots.graph_edges` 已持久化但恒空，故本图为仓库粒度；任务级依赖边另立项。
      </div>
    </div>
  );
}

/** 单仓悬浮环境窗。骨架阶段只出结构与作用域声明——变更/CHANGESET/基线来自 v0.1
 *  交付聚合（轮次粒度），需 issue 详情页与轮次打通后才能取到本仓切片，此前不填假数。 */
function EnvFloat({ repositoryName }: { repositoryName: string }) {
  const [min, setMin] = useState(false);

  return (
    <aside className="fixed top-[64px] right-4 z-[8] max-h-[calc(100vh-90px)] w-[252px] overflow-y-auto rounded-hard border border-line bg-[#1c1710] shadow-[0_12px_30px_rgba(0,0,0,0.5)]">
      <div className="sticky top-0 flex items-center border-b border-line bg-[#1c1710] px-3 py-2.5">
        <span className="font-mono text-[11px] tracking-[0.16em] text-tx">环境 · {repositoryName}</span>
        <button className="ml-auto px-0.5 text-[13px] text-tx2 hover:text-amber-hi" onClick={() => setMin((v) => !v)}>
          {min ? "▸" : "▾"}
        </button>
      </div>
      {!min && (
        <div className="px-3 py-2.5">
          <div className="microlabel pb-1.5">作用域</div>
          <p className="text-[11.5px] leading-[1.6] text-tx2">
            本窗为<b className="text-tx">单仓</b>作用域，只呈现当前房间所属仓库的环境。
          </p>
          <div className="microlabel pt-3 pb-1.5">变更 · CHANGESET · 基线</div>
          <p className="text-[11.5px] leading-[1.6] text-[#6b6046]">
            数据来自 v0.1 交付聚合（轮次粒度的 diffs / change_set / repositories），需 issue
            详情页与轮次打通后才能取本仓切片。骨架阶段不填占位数字。
          </p>
        </div>
      )}
    </aside>
  );
}

export function RoomView({
  room,
  stream,
  plan,
  onBack,
  onToast,
}: {
  room: RoomListItem;
  stream: RoomStreamPage;
  plan: RepositoryPlan;
  onBack: () => void;
  onToast: (text: string) => void;
}) {
  const [view, setView] = useState<"chat" | "plan">("chat");

  const members = room.members.map((m) => m.name ?? `AGENT ${m.agent_id.slice(0, 8)}`).join("，");
  const tabBase = "px-3 py-[5px] text-[11.5px]";

  return (
    <div className="pr-[268px]">
      <div className="-mx-8 -mt-5 mb-3.5 flex items-center gap-2.5 border-b border-line bg-panel px-8 py-3">
        <button className="text-[11.5px] text-tx2 hover:text-tx" onClick={onBack}>
          ‹
        </button>
        <span className="grid size-7 flex-none place-items-center rounded-hard bg-line font-mono text-[10.5px] text-kraft">
          {room.kind === "team_room" ? "TR" : "DM"}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[13px] text-tx">
              {room.repository_name} · {room.kind === "team_room" ? "teamRoom" : "leaderDM"}
            </span>
            {room.live && (
              <span
                className="flex-none rounded-hard border border-salmon px-1.5 font-mono text-[10px] tracking-[0.1em] text-salmon"
                title="LIVE 由该仓在途任务派生，不是 Matrix presence（契约 v0.2 §5.3）"
              >
                <i className="blink mr-1 inline-block size-[5px] rounded-full bg-salmon align-middle not-italic" />
                LIVE
              </span>
            )}
          </div>
          {/* 刷新机制必须显式标注：当前是 replay 夹具，不是轮询，更不是推送 */}
          <div className="truncate text-[10.5px] text-tx2">
            {members} · replay 夹具（轮询待 CONS-33 上线后接入；SSE 另立项）
          </div>
        </div>

        <div className="flex flex-none overflow-hidden rounded-hard border border-line">
          <button
            className={view === "chat" ? `${tabBase} bg-amber font-bold text-[#191308]` : `${tabBase} text-tx2`}
            onClick={() => setView("chat")}
          >
            房间
          </button>
          <button
            className={view === "plan" ? `${tabBase} bg-amber font-bold text-[#191308]` : `${tabBase} text-tx2`}
            onClick={() => setView("plan")}
          >
            DAG · PLAN · SPEC
          </button>
        </div>
      </div>

      {view === "chat" ? (
        <div className="max-w-[640px]">
          {stream.items.length === 0 ? (
            /* 空房间不装满（§5.1） */
            <p className="py-8 text-center text-[12.5px] text-[#6b6046]">暂无消息</p>
          ) : (
            stream.items.map((item) => <StreamEntry key={item.payload_ref ?? item.at} item={item} />)
          )}

          {stream.next_cursor ? (
            <button
              className="mt-3 rounded-hard border border-line px-3 py-1 text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
              onClick={() => onToast("续读待 CONS-33 房间流端点接入")}
            >
              ↓ 加载后续
            </button>
          ) : (
            <p className="pt-3 text-[11px] text-[#6b6046]">
              系统条目（治理决策 / 门禁 / RUNNER）是控制台投影，非房间内真实发生。
            </p>
          )}
        </div>
      ) : (
        <PlanPaper plan={plan} />
      )}

      <EnvFloat repositoryName={room.repository_name} />
    </div>
  );
}
