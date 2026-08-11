import { useEffect, useRef, useState } from "react";
import type { RepositoryPlanView, RoomListItemView, RoomStreamPage } from "../api/contract";
import type { RepositoryEnv } from "../types";
import {
  ROOM_POLL_MS,
  fetchRepositoryEnv,
  fetchRepositoryPlan,
  fetchRoomStream,
  fetchRooms,
} from "../api/rooms";
import { resolveDataSourceMode } from "../api/source";
import { RoomView } from "./RoomView";

/** 房间视图取数容器（§5.1 元数据 + §5.2 流 + §5.4 纸面 + 环境窗切片）。
 *
 *  房间元数据从所属 issue 的房间清单里取，而不是靠调用方传——直接粘 URL 进来时
 *  没有上游状态可依赖。未知 room_id 在清单里找不到，呈现明确的「不存在」而非空白。
 *
 *  刷新机制 = **轮询**（§5.3；SSE 另立项）。三条约束：页面不可见时跳过一轮，
 *  上一轮未回来时不叠加请求，轮询失败保留已有内容只更新新鲜度标注——刷新失败
 *  不该把读者正在看的消息清空。 */
export function RoomViewContainer({
  issueId,
  roomId,
  onBack,
  onToast,
}: {
  issueId: string;
  roomId: string;
  onBack: () => void;
  onToast: (text: string) => void;
}) {
  const [room, setRoom] = useState<RoomListItemView | null>(null);
  const [stream, setStream] = useState<RoomStreamPage | null>(null);
  const [plan, setPlan] = useState<RepositoryPlanView | null>(null);
  const [env, setEnv] = useState<RepositoryEnv | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  /** 最后一次成功刷新的时刻；null = 尚未轮询过 */
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null);
  const [staleNote, setStaleNote] = useState<string | null>(null);
  const inFlight = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setPlan(null);
    setEnv(null);
    setRefreshedAt(null);
    setStaleNote(null);

    fetchRooms(issueId)
      .then(async (rooms) => {
        const found = rooms.find((r) => r.room_id === roomId);
        if (!found) throw new Error(`房间 ${roomId} 不在该 issue 的房间清单内`);
        const page = await fetchRoomStream(roomId);
        if (cancelled) return;
        setRoom(found);
        setStream(page);
        setLoading(false);
        setRefreshedAt(new Date());

        // 第二视图与环境窗独立取用：任一失败都不该挡住聊天流
        fetchRepositoryPlan(issueId, found.repository_id)
          .then((p) => !cancelled && setPlan(p))
          .catch((err: unknown) => {
            if (!cancelled) onToast(`DAG·PLAN·SPEC 取用失败：${err instanceof Error ? err.message : String(err)}`);
          });
        fetchRepositoryEnv(issueId, found.repository_id)
          .then((e) => !cancelled && setEnv(e))
          .catch((err: unknown) => {
            if (!cancelled) onToast(`环境窗取用失败：${err instanceof Error ? err.message : String(err)}`);
          });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [issueId, roomId, reload, onToast]);

  // 轮询：只在已成功首载后启动；replay 无远端可轮询，直接不起
  const polling = room !== null && resolveDataSourceMode() === "live";
  useEffect(() => {
    if (!polling) return;
    let cancelled = false;

    const tick = () => {
      if (document.visibilityState !== "visible") return; // 后台标签页不空转打后端
      if (inFlight.current) return; // 上一轮未回来就跳过，不叠加
      inFlight.current = true;
      fetchRoomStream(roomId)
        .then((page) => {
          if (cancelled) return;
          setStream(page);
          setRefreshedAt(new Date());
          setStaleNote(null);
        })
        .catch((err: unknown) => {
          // 刷新失败保留已有内容，只标注新鲜度——不把正在读的消息清空
          if (!cancelled) setStaleNote(err instanceof Error ? err.message : String(err));
        })
        .finally(() => {
          inFlight.current = false;
        });
    };

    const id = window.setInterval(tick, ROOM_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [polling, roomId]);

  if (loading) return <p className="py-8 text-center text-[12.5px] text-tx2">加载房间…</p>;

  if (error || !room || !stream) {
    return (
      <div className="max-w-[860px]">
        <button className="pb-3 text-[11.5px] text-tx2 hover:text-tx" onClick={onBack}>
          ‹ 返回 issue
        </button>
        <div className="rounded-hard border border-salmon/60 bg-salmon/10 px-4 py-3">
          <div className="eyebrow mb-1 text-salmon">房间加载失败</div>
          <p className="text-[12px] text-salmon">{error ?? "未取到房间"}</p>
          <button
            className="mt-2 rounded-hard border border-line px-2.5 py-[3px] text-[11.5px] text-tx2 hover:border-amber hover:text-amber-hi"
            onClick={() => setReload((n) => n + 1)}
          >
            重试
          </button>
        </div>
      </div>
    );
  }

  const clock = refreshedAt
    ? `${String(refreshedAt.getHours()).padStart(2, "0")}:${String(refreshedAt.getMinutes()).padStart(2, "0")}:${String(refreshedAt.getSeconds()).padStart(2, "0")}`
    : "—";
  const sourceNote = polling
    ? staleNote
      ? `live · 轮询 ${ROOM_POLL_MS / 1000}s · 刷新失败（末次 ${clock}）：${staleNote.slice(0, 40)}`
      : `live · 轮询 ${ROOM_POLL_MS / 1000}s · 末次刷新 ${clock}`
    : "replay 夹具（非实时）";

  return (
    <RoomView
      room={room}
      stream={stream}
      plan={plan}
      env={env}
      sourceNote={sourceNote}
      onBack={onBack}
      onToast={onToast}
    />
  );
}
