import { useEffect, useRef, useState } from "react";
import { Bot, Maximize2, X } from "lucide-react";
import type { RoomListItemView, RoomStreamPage } from "../../api/contract";
import {
  ROOM_POLL_MS,
  fetchRoomStream,
  fetchRoundEvents,
  fetchRoundId,
} from "../../api/rooms";
import { resolveDataSourceMode } from "../../api/source";
import { LoadingLine } from "../../components/StatusBlocks";
import { agentLabel, errText, eventTime } from "../../display";

/** 工作台右栏的仓库房间面板（期 3）。
 *
 *  **只读**（B 定稿）：房间切换、成员与活跃态、消息流、轮次事件折叠段。
 *  写路径（点名/发消息）留在「⤢ 放大」跳转的全页 RoomView 里——面板的职责是
 *  「这个仓库正在发生什么」，塞进写路径只会把窄栏变成第二个操作台。
 *
 *  两条契约红线照搬：
 *   - §5.2 Q4：只有 `message !== null` 的条目才配气泡；governance/gate/runner
 *     是控制台投影事实，一律系统条目样式；
 *   - §5.3：`live` 是「有进行中任务」的派生，**不是在线状态**，文案不写「在线」。
 *
 *  轮询与全页容器同一套约束：页面不可见跳过、上一轮未回来不叠加、失败保留已有
 *  内容只标注新鲜度。 */
export function RoomPanel({
  issueId,
  rooms,
  selectedRoomId,
  onSelectRoom,
  onClose,
  onExpand,
}: {
  issueId: string;
  /** 全量房间清单（team_room + leader_dm）；面板只把 team_room 摆上切换位 */
  rooms: RoomListItemView[];
  selectedRoomId: string;
  onSelectRoom: (roomId: string) => void;
  onClose: () => void;
  /** 放大到全页房间视图 */
  onExpand: () => void;
}) {
  const room = rooms.find((r) => r.room_id === selectedRoomId) ?? null;
  const teamRooms = rooms.filter((r) => r.kind === "team_room");

  const [stream, setStream] = useState<RoomStreamPage | null>(null);
  const [staleNote, setStaleNote] = useState<string | null>(null);
  const [streamLoading, setStreamLoading] = useState(true);
  const inFlight = useRef(false);

  const [roundId, setRoundId] = useState<string | null>(null);
  const [events, setEvents] = useState<Array<{ at: string | null; kind: string; text: string }> | null>(null);

  // 换房间：流复位重取；轮次事件随之重解析
  useEffect(() => {
    let cancelled = false;
    setStream(null);
    setStaleNote(null);
    setStreamLoading(true);
    setRoundId(null);
    setEvents(null);
    fetchRoomStream(selectedRoomId)
      .then((page) => {
        if (cancelled) return;
        setStream(page);
        setStreamLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStaleNote(errText(err));
        setStreamLoading(false);
      });
    if (resolveDataSourceMode() === "live") {
      fetchRoundId(issueId)
        .then((rid) => {
          if (cancelled) return;
          setRoundId(rid);
          if (!rid) {
            setEvents([]);
            return;
          }
          fetchRoundEvents(rid)
            .then((page) => !cancelled && setEvents(page.items.map((e) => ({ at: e.at, kind: e.kind, text: e.text }))))
            .catch(() => !cancelled && setEvents([]));
        })
        .catch(() => !cancelled && setEvents([]));
    }
    return () => {
      cancelled = true;
    };
  }, [selectedRoomId, issueId]);

  // 轮询：live 且面板开着才转；不可见跳过、在途不叠加、失败不清屏只标新鲜度
  useEffect(() => {
    if (resolveDataSourceMode() !== "live") return;
    let cancelled = false;
    const tick = () => {
      if (document.visibilityState !== "visible") return;
      if (inFlight.current) return;
      inFlight.current = true;
      fetchRoomStream(selectedRoomId)
        .then((page) => {
          if (cancelled) return;
          setStream(page);
          setStaleNote(null);
        })
        .catch((err: unknown) => {
          if (!cancelled) setStaleNote(errText(err));
        })
        .finally(() => {
          inFlight.current = false;
        });
    };
    const timer = window.setInterval(tick, ROOM_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedRoomId]);

  // 面板消息流自动滚底（与对话流同一交互约定：贴底跟随，上翻不抢）
  const streamRef = useRef<HTMLDivElement | null>(null);
  const nearBottomRef = useRef(true);
  useEffect(() => {
    const el = streamRef.current;
    if (!el) return;
    if (nearBottomRef.current) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [stream]);

  if (!room) {
    return (
      <div className="flex h-full w-[352px] flex-col">
        <PanelHeader title="房间" onClose={onClose} onExpand={undefined} />
        <div className="flex flex-1 items-center justify-center bg-well px-6 text-[11.5px] text-tx2">
          房间不在清单里（可能已被清理）。
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full w-[352px] flex-col">
      <div className="border-b border-line px-3.5 pb-2.5 pt-2.5">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[13px] font-bold text-cream">{room.repository_name ?? "未命名仓库"}</span>
          <span
            className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-px font-mono text-[9.5px] text-tx2"
            title="live = 该仓有进行中的任务（§5.3 派生，不是在线状态）"
          >
            <span className={`h-1.5 w-1.5 rounded-full ${room.live ? "bg-olive" : "bg-tx3"}`} />
            {room.live ? "任务进行中" : "无进行中任务"}
          </span>
          <div className="ml-auto flex gap-1.5">
            <button
              className="h-6 w-6 rounded-hard border border-line-strong text-[11px] text-tx2 hover:border-amber hover:text-tx"
              title="放大到全页房间视图"
              onClick={onExpand}
            >
              <Maximize2 size={11} />
            </button>
            <button
              className="h-6 w-6 rounded-hard border border-line-strong text-[11px] text-tx2 hover:border-amber hover:text-tx"
              title="收起"
              onClick={onClose}
            >
              <X size={12} />
            </button>
          </div>
        </div>
        {teamRooms.length > 1 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {teamRooms.map((r) => (
              <button
                key={r.room_id}
                className={`rounded-full border px-2.5 py-px font-mono text-[10.5px] ${
                  r.room_id === selectedRoomId
                    ? "border-amber bg-amber text-on-amber"
                    : "border-line-strong bg-panel text-tx2 hover:border-amber"
                }`}
                onClick={() => onSelectRoom(r.room_id)}
              >
                {r.repository_name ?? "未命名"}
              </button>
            ))}
          </div>
        )}
        <div className="mt-2 flex flex-wrap gap-1.5">
          {room.members.map((m) => (
            <span
              key={m.agent_id}
              className="inline-flex items-center gap-1 rounded-full border border-line bg-well px-2 py-px text-[10px] text-tx2"
              title={`${m.role} · ${m.agent_id}`}
            >
              <Bot size={10} strokeWidth={1.5} className="flex-none text-tx2" />
              {m.name ?? m.agent_id.slice(0, 8)}
            </span>
          ))}
          {room.members.length === 0 && <span className="text-[10px] text-tx3">成员未回报</span>}
        </div>
      </div>

      <div
        ref={streamRef}
        onScroll={() => {
          const el = streamRef.current;
          if (el) nearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
        }}
        className="min-h-0 flex-1 overflow-y-auto bg-well px-3 py-2.5"
      >
        {streamLoading && <LoadingLine text="加载房间消息…" />}
        {!streamLoading && staleNote && (
          <p className="px-1 pb-2 text-[10.5px] text-salmon">刷新失败（保留已载内容）：{staleNote}</p>
        )}
        {!streamLoading && stream && stream.items.length === 0 && (
          <p className="px-1 py-3 text-[11px] leading-[1.8] text-tx2">
            房间还没有消息。派单、点名与汇报会出现在这里——第一条消息通常在任务派出后几分钟内。
          </p>
        )}
        {stream?.items.map((item) =>
          item.message !== null ? (
            <PanelMessage key={item.message.id} item={item} />
          ) : (
            <p key={item.payload_ref ?? item.at} className="border-l-2 border-line px-2 py-1 font-mono text-[10.5px] leading-[1.6] text-tx2">
              <span className="text-tx3">{eventTime(item.at).slice(0, 5)} </span>
              {item.text ?? "（无摘要的控制台投影）"}
            </p>
          ),
        )}
      </div>

      {events !== null && (
        <details className="border-t border-line bg-panel">
          <summary className="cursor-pointer select-none px-3.5 py-2 text-[11.5px] font-semibold text-tx2">
            事件时间线（当前轮次）
          </summary>
          <ul className="max-h-[180px] overflow-y-auto px-3.5 pb-2.5">
            {(events ?? []).length === 0 && (
              <li className="py-1 text-[10.5px] text-tx3">没有轮次事件{roundId === null ? "（尚无轮次）" : ""}。</li>
            )}
            {(events ?? []).map((e, i) => (
              <li key={`${e.at ?? i}-${i}`} className="flex gap-2 py-0.5 text-[10.5px] text-tx2">
                <span className="flex-none font-mono text-tx3">{eventTime(e.at).slice(0, 5)}</span>
                <span className="min-w-0 truncate">
                  <span className="font-mono text-tx3">[{e.kind}]</span> {e.text}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function PanelHeader({ title, onClose, onExpand }: { title: string; onClose: () => void; onExpand?: () => void }) {
  return (
    <div className="border-b border-line px-3.5 pb-2.5 pt-2.5">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[13px] font-bold text-cream">{title}</span>
        <div className="ml-auto flex gap-1.5">
          {onExpand && (
            <button className="h-6 w-6 rounded-hard border border-line-strong text-[11px] text-tx2" onClick={onExpand}>
              <Maximize2 size={11} />
            </button>
          )}
          <button className="h-6 w-6 rounded-hard border border-line-strong text-[11px] text-tx2" onClick={onClose}>
            <X size={12} />
          </button>
        </div>
      </div>
    </div>
  );
}

/** 窄栏版气泡：与 RoomView 同一派生（agentLabel / 降级链），去掉头像省宽度。
 *  仅在 `message !== null` 的分支调用（§5.2 判据在调用方）。 */
function PanelMessage({ item }: { item: import("../../api/contract").RoomStreamItemView }) {
  const m = item.message;
  if (!m) return null;
  const who =
    m.sender_agent_id === null
      ? (m.sender_name ?? "发送者未解析")
      : agentLabel(m.sender_name, m.sender_agent_id);
  return (
    <div className="py-1.5">
      <div className="flex items-baseline gap-2">
        <span className="text-[11px] font-semibold text-amber">{who}</span>
        <span className="rounded-hard border border-line px-1.5 font-mono text-[9.5px] text-tx2">{m.kind}</span>
        <span className="ml-auto font-mono text-[10px] text-tx3">{eventTime(item.at).slice(0, 5)}</span>
      </div>
      <div className="mt-1 rounded-hard border border-line bg-panel px-2.5 py-1.5 text-[11.5px] leading-[1.6] text-tx">
        {m.body}
        {m.recipient_name && <span className="text-tx2">（→ {m.recipient_name}）</span>}
      </div>
    </div>
  );
}
