import { useState } from "react";
import type {
  DeliveryEventKind,
  GateDisplay,
  RepositoryPlanView,
  RoomListItemView,
  RoomStreamItemView,
  RoomStreamPage,
} from "../api/contract";
import type { RepositoryEnv } from "../types";
import { EventTimeline, type EventsTimelineState } from "../components/EventTimeline";
import { repositoryLabel } from "../display";
import { eventTime } from "../viewmodel";

/** 活体房间视图（CONS-43 骨架）。版式按原型 `#v-room`：
 *  房间头（成员 + LIVE + 刷新机制标注）→ 双视图切换（房间聊天 ↔ DAG·PLAN·SPEC）
 *  → 单仓悬浮环境窗。
 *
 *  ⚠ 本文件承载契约 v0.2 §5.2 + Q4 的**硬约束**：
 *     只有房间内**真实发生**的消息才渲染为聊天气泡（头像 + 发送者）；
 *     governance / gate / runner 是控制台**投影事实**，必须渲染为系统条目，
 *     **无头像、无发送者名**——不得让用户以为某个 agent 在房间里说过这句话。
 *  这是契约文本要求，不是渲染建议。分流点只有一处（见 StreamEntry），判据是
 *  `message !== null`；新增 source 值时漏判只会退化成系统条目，不会退化成假气泡。 */

/** 非 message 源的展示皮肤。配色沿用 CONS-14 事件时间线的既有令牌，零新色值。
 *  bluegray 保留给「执行中」语义，此处不用。 */
const SOURCE_SKIN: Record<Exclude<RoomStreamItemView["source"], "message">, { label: string; skin: string }> = {
  governance: { label: "治理决策", skin: "border-kraft text-kraft" },
  gate: { label: "门禁", skin: "border-olive text-olive" },
  runner: { label: "RUNNER", skin: "border-amber text-amber" },
};

function MessageBubble({ item }: { item: RoomStreamItemView }) {
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
function SystemEntry({ item }: { item: RoomStreamItemView }) {
  // 未知 source 也要能渲染成系统条目（不能崩，更不能掉回气泡）
  const skin =
    SOURCE_SKIN[item.source as Exclude<RoomStreamItemView["source"], "message">] ??
    ({ label: item.source.toUpperCase(), skin: "border-line text-tx2" } as const);
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

/** 唯一分流点（契约 §5.2）。判据是 **`message !== null`** 而非比对 source 字符串：
 *  后端保证投影条目由一个无法附加 message 载荷的构造函数生成，故非 message 源恒 null
 *  （契约 §7.2）。这样将来新增 source 值时，漏判只会退化成系统条目——不会退化成
 *  一个假装某 agent 说过话的气泡。 */
function StreamEntry({ item }: { item: RoomStreamItemView }) {
  return item.message !== null ? <MessageBubble item={item} /> : <SystemEntry item={item} />;
}

function PlanPaper({ plan }: { plan: RepositoryPlanView }) {
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

const GATE_SKIN: Record<GateDisplay, string> = {
  open: "text-olive",
  blocked: "text-salmon",
  running: "text-bluegray",
  waiting: "text-tx2",
};

const GATE_LABEL: Record<GateDisplay, string> = {
  open: "可合并",
  blocked: "门禁受阻",
  running: "门禁运行中",
  waiting: "等待中",
};

/** 单仓悬浮环境窗：轮次粒度的交付聚合切到本仓。
 *  §6.3 diffstat 为 null，所以只列变更文件名、**不编 ± 行数**（原型里的 +412 是设计稿）。
 *
 *  末段是事件时间线（CONS-14 从 v1 环境窗迁入）。**这里有个粒度落差要如实处理**：
 *  `/deliveries/{id}/events` 是**轮次粒度**（跨全部仓库），而本窗是**单仓作用域**。
 *  三种处理里选了第三种：
 *   - 全显示 → 单仓窗口里混进别仓事件，破坏这个窗口的作用域约定；
 *   - 只显示本仓 → 丢掉 `repository_id: null` 的轮次级事实（实测种子里「计划 v1 已生成」
 *     就是这一类），而它对本仓同样成立；
 *   - **显示「本仓 + 不分仓」，并把被折起的别仓条数说出来**，可一键展开看全部。
 *
 *  折叠是**当页语义**：`?kind=` 是服务端过滤（全量），仓库维度 §4.1 没有定义参数，
 *  只能在已加载的这一页里分拣。所以文案写「本页另有 N 条」而不是「另有 N 条」——
 *  分页之下把部分结果说成全量，是 issue 列表撤掉两个筛选按钮时定下的同一条红线。 */
function EnvFloat({
  repositoryName,
  repositoryId,
  env,
  events,
  eventsDemo,
  hasRound,
  onEventsFilter,
  onEventsMore,
}: {
  repositoryName: string;
  repositoryId: string;
  env: RepositoryEnv | null;
  events: EventsTimelineState;
  eventsDemo: boolean;
  hasRound: boolean;
  onEventsFilter: (kind: DeliveryEventKind | null) => void;
  onEventsMore: () => void;
}) {
  const [min, setMin] = useState(false);
  const [allRepos, setAllRepos] = useState(false);

  const scoped = events.items.filter((e) => e.repository_id === repositoryId || e.repository_id === null);
  const otherCount = events.items.length - scoped.length;
  const shown = allRepos ? events : { ...events, items: scoped };

  return (
    <aside className="fixed top-[64px] right-4 z-[8] max-h-[calc(100vh-90px)] w-[252px] overflow-y-auto rounded-hard border border-line bg-[#1c1710] shadow-[0_12px_30px_rgba(0,0,0,0.5)]">
      <div className="sticky top-0 flex items-center border-b border-line bg-[#1c1710] px-3 py-2.5">
        <span className="font-mono text-[11px] tracking-[0.16em] text-tx">环境 · {repositoryName}</span>
        <button className="ml-auto px-0.5 text-[13px] text-tx2 hover:text-amber-hi" onClick={() => setMin((v) => !v)}>
          {min ? "▸" : "▾"}
        </button>
      </div>

      {!min && (
        <>
          {env === null ? (
            <p className="px-3 py-2.5 text-[11.5px] leading-[1.6] text-[#6b6046]">
              本仓环境未接入：该 issue 尚无轮次，或本仓不在当前轮次的交付聚合内。
            </p>
          ) : (
          <div className="px-3 py-2.5">
            <div className="microlabel pb-1.5">状态</div>
            <div className="flex items-baseline gap-2 font-mono text-[11.5px]">
              {/* gate_display 原样透传，前端不映射 */}
              <span className={env.gateDisplay ? GATE_SKIN[env.gateDisplay] : "text-tx2"}>
                {env.gateDisplay ? GATE_LABEL[env.gateDisplay] : "未进入变更集"}
              </span>
              {env.mergeAllowed === null && env.gateDisplay && (
                <span className="text-[10px] text-[#6b6046]">合并请求已发出</span>
              )}
            </div>

            <div className="microlabel pt-3 pb-1">变更</div>
            {env.changedFiles.length === 0 ? (
              <p className="text-[11px] text-[#6b6046]">尚无变更</p>
            ) : (
              <>
                {env.changedFiles.slice(0, 8).map((f) => (
                  <div key={f.path} className="truncate pl-1 font-mono text-[10.5px] text-tx2" title={f.path}>
                    {f.path}
                  </div>
                ))}
                {env.changedFiles.length > 8 && (
                  <div className="pl-1 text-[10.5px] text-[#6b6046]">+{env.changedFiles.length - 8} 个文件</div>
                )}
                {/* §6.3：diffstat 无源，不编 ± 行数 */}
                <div className="pt-1 pl-1 text-[10px] text-[#6b6046]">± 行数未接入（diffstat 无源）</div>
              </>
            )}
            {/* 这行是候选 commit 列表，不是 diffstat：`±` 属于增删行数的语汇，
                用在 sha 前会读成「改了 8825f6bb 行」。前缀改为自述的 commit。 */}
            {env.commitShas.length > 0 && (
              <div className="pt-1 pl-1 font-mono text-[10.5px] text-[#6b6046]">
                commit {env.commitShas.join(" · ")}
              </div>
            )}

            <div className="microlabel pt-3 pb-1">CHANGESET · 本仓位置</div>
            {env.siblings.length === 0 ? (
              <p className="text-[11px] text-[#6b6046]">本轮无变更集</p>
            ) : (
              env.siblings.map((s) => (
                <div
                  key={s.name}
                  className={`flex items-baseline gap-2 px-1 py-0.5 font-mono text-[11px] ${
                    s.isCurrent ? "bg-[#241c10]" : ""
                  }`}
                >
                  <span className={s.isCurrent ? "text-tx" : "text-tx2"}>{s.name}</span>
                  <span className={`ml-auto text-[10.5px] ${GATE_SKIN[s.gate]}`}>{GATE_LABEL[s.gate]}</span>
                  {s.isCurrent && <span className="text-[10px] text-amber">◂ 当前</span>}
                </div>
              ))
            )}

            <div className="microlabel pt-3 pb-1">环境</div>
            <div className="flex items-baseline gap-2 px-1 font-mono text-[11px]">
              <span className="text-tx2">PR</span>
              <span className="ml-auto text-[10.5px] text-tx">
                {env.prUrl ? (
                  <a className="underline hover:text-amber-hi" href={env.prUrl} target="_blank" rel="noreferrer">
                    {env.prLabel}
                  </a>
                ) : (
                  (env.prLabel ?? "—")
                )}
              </span>
            </div>
            <div className="flex items-baseline gap-2 px-1 pb-1 font-mono text-[11px]">
              <span className="text-tx2">基线快照</span>
              <span className="ml-auto truncate text-[10.5px] text-[#6b6046]">
                {env.validationSnapshotId ? env.validationSnapshotId.slice(0, 8) : "未接入"}
              </span>
            </div>
            </div>
          )}

          {/* 事件时间线独立于 env：本仓不在聚合内（env 为 null）时本轮事件依然成立 */}
          <div className="border-t border-line pt-1 pb-2">
            <div className="microlabel px-3 pt-2 pb-1">
              事件时间线 · 本轮
              <span className="ml-1.5 tracking-normal text-[#6b6046] normal-case">
                （轮次粒度，跨全部仓库）
              </span>
            </div>

            {!hasRound ? (
              <p className="px-3 py-1 text-[11px] text-[#6b6046]">该 issue 尚无轮次，没有事件可读。</p>
            ) : (
              <>
                <EventTimeline
                  state={shown}
                  demo={eventsDemo}
                  onFilter={onEventsFilter}
                  onLoadMore={onEventsMore}
                />
                {otherCount > 0 && (
                  <button
                    className="mx-3 mt-1 text-left text-[10.5px] text-[#6b6046] underline hover:text-amber-hi"
                    onClick={() => setAllRepos((v) => !v)}
                  >
                    {allRepos
                      ? `收起其他仓事件（本页 ${otherCount} 条）`
                      : `本页另有 ${otherCount} 条其他仓事件 · 展开`}
                  </button>
                )}
              </>
            )}
          </div>
        </>
      )}
    </aside>
  );
}

export function RoomView({
  room,
  stream,
  plan,
  env,
  events,
  eventsDemo,
  hasRound,
  sourceNote,
  onBack,
  onToast,
  onEventsFilter,
  onEventsMore,
}: {
  room: RoomListItemView;
  stream: RoomStreamPage;
  /** 第二视图的数据独立取用，未到或失败时为 null——不挡住聊天流 */
  plan: RepositoryPlanView | null;
  /** 单仓环境切片；未取到为 null，窗内显缺口不填假数 */
  env: RepositoryEnv | null;
  /** 本轮事件时间线（CONS-14）。轮次粒度，落位与折叠规则见 EnvFloat 头注 */
  events: EventsTimelineState;
  /** 回放模式：deny 是治理拦截演示；live 收到 deny 即契约违约警示（§6.6） */
  eventsDemo: boolean;
  /** 该 issue 是否有轮次——无轮次时事件段显缺口，而不是空列表冒充「没有事件」 */
  hasRound: boolean;
  /** 数据来源与刷新机制的实况标注。**必须与实际一致**：写着 replay 却在放 live
   *  数据，比不标注更糟——那是在说谎。由取数容器按数据源传入。 */
  sourceNote: string;
  onBack: () => void;
  onToast: (text: string) => void;
  onEventsFilter: (kind: DeliveryEventKind | null) => void;
  onEventsMore: () => void;
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
              {repositoryLabel(room.repository_name, room.repository_id)} ·{" "}
              {room.kind === "team_room" ? "teamRoom" : "leaderDM"}
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
          {/* 数据源与刷新机制显式标注，由容器按实况传入 */}
          <div className="truncate text-[10.5px] text-tx2">
            {members} · {sourceNote}
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
      ) : plan ? (
        <PlanPaper plan={plan} />
      ) : (
        <p className="py-8 text-[12.5px] text-tx2">DAG · PLAN · SPEC 取用中…</p>
      )}

      <EnvFloat
        repositoryName={repositoryLabel(room.repository_name, room.repository_id)}
        repositoryId={room.repository_id}
        env={env}
        events={events}
        eventsDemo={eventsDemo}
        hasRound={hasRound}
        onEventsFilter={onEventsFilter}
        onEventsMore={onEventsMore}
      />
    </div>
  );
}
